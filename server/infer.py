"""One isolated inference process per job: releases model RAM/VRAM on exit."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
from server.observability import package_versions
from server.catalog import installed_models
from server.metrics import make_metrics
from server.segment_timing import reconcile_whisper_segments
from server.config import getenv

MAX_MEDIA_BYTES = 90 * 1024**2
MAX_AUDIO_SECONDS = 7200


def bounded_audio_duration(container, maximum=MAX_AUDIO_SECONDS):
    """Count decoded audio, not merely untrusted container duration metadata.

    Runs before model allocation/inference. Streams frames without retaining
    PCM. It intentionally costs a separate decode pass to cap ASR work.
    """
    stream = next((item for item in container.streams if item.type == 'audio'), None)
    if stream is None:
        raise ValueError('Arquivo sem faixa de áudio.')
    seconds = 0.0
    for frame in container.decode(stream):
        rate = getattr(frame, 'sample_rate', None) or getattr(stream.codec_context, 'sample_rate', 0)
        count = getattr(frame, 'samples', 0)
        if not rate or count <= 0:
            raise ValueError('Amostras de áudio inválidas.')
        seconds += count / rate
        if seconds > maximum:
            raise ValueError('Duração máxima: 2 horas.')
    if seconds <= 0:
        raise ValueError('Áudio vazio.')
    return seconds


DLL_HANDLES=[]
if os.name=='nt':
    import site
    for root in site.getsitepackages():
        for name in ['cublas','cudnn']:
            directory=Path(root)/'nvidia'/name/'bin'
            if directory.is_dir():
                DLL_HANDLES.append(os.add_dll_directory(str(directory)))
                os.environ['PATH']=str(directory)+os.pathsep+os.environ['PATH']

def run(config):
    folder=Path(config).parent
    job=json.loads(Path(config).read_text(encoding='utf-8'))
    started=time.perf_counter()
    stage_started=started
    stages={}
    def finish_stage(name):
        nonlocal stage_started
        now=time.perf_counter()
        stages[name]=round(now-stage_started,6)
        stage_started=now
    proc=psutil.Process()
    peaks={'peak_rss_mb':0.0,'peak_vram_mb':0.0}
    done=threading.Event()
    def sample():
        while not done.wait(1):
            peaks['peak_rss_mb']=max(peaks['peak_rss_mb'],proc.memory_info().rss/1024**2)
            try:
                if job['device']=='cuda':
                    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
                    r=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,**flags)
                    for line in r.stdout.splitlines():
                        pid,mb=line.split(',')
                        if int(pid)==os.getpid(): peaks['peak_vram_mb']=max(peaks['peak_vram_mb'],float(mb))
            except (OSError,ValueError,subprocess.TimeoutExpired): pass
    thread=threading.Thread(target=sample,daemon=True)
    thread.start()
    result={}
    media_title=None
    def progress(stage,value=0):
        temp=folder/'progress.tmp'
        temp.write_text(json.dumps(dict(stage=stage,progress=min(99,max(0,value)))),encoding='utf-8')
        temp.replace(folder/'progress.json')
    try:
        media=folder/'input.media'
        if job['source']!='upload':
            progress('downloading')
            import yt_dlp
            def limit(d):
                if d.get('downloaded_bytes',0)>90*1024**2:
                    raise ValueError('Download excede 90 MB.')
            with yt_dlp.YoutubeDL({'js_runtimes':{'node':{}},'format':'bestaudio/best','noplaylist':True,'outtmpl':str(media),'max_filesize':MAX_MEDIA_BYTES,'socket_timeout':30,'retries':2,'quiet':True,'progress_hooks':[limit],'match_filter':lambda info,**kw: 'Duração máxima: 2 horas.' if (info.get('duration') or 0)>7200 or info.get('is_live') else None}) as ydl:
                media_info=ydl.extract_info(job['source'],download=True)
                if media_info: media_title=(media_info.get('title') or 'YouTube')[:180]
        if job['source']!='upload':
            finish_stage('download_s')
        else:
            stage_started=time.perf_counter()  # Transport happened in the worker.
        # Never allocate a model for oversized or overlong media. A complete
        # lightweight decode checks actual PCM duration when metadata lies.
        if not media.is_file() or media.stat().st_size > MAX_MEDIA_BYTES:
            raise ValueError('Mídia excede o limite de 90 MB.')
        import av
        with av.open(str(media)) as container:
            duration=(container.duration or 0)/av.time_base
            if duration>MAX_AUDIO_SECONDS: raise ValueError('Duração máxima: 2 horas.')
            decoded_duration = bounded_audio_duration(container)
        finish_stage('media_probe_s')
        from faster_whisper import WhisperModel
        progress('loading')
        model_root=getenv('STT_MODELS',str(Path(__file__).resolve().parents[1]/'models'))
        model=WhisperModel(job['model'],device=job['device'],compute_type=job['compute'],cpu_threads=8,download_root=model_root)
        finish_stage('model_load_s')
        revisions={m['id']:m['revision'] for m in installed_models(model_root)}
        progress('transcribing')
        segments,info=model.transcribe(str(media),beam_size=5,vad_filter=True)
        if info.duration>7200: raise ValueError('Duração máxima: 2 horas.')
        output=[]
        for segment in segments:
            if segment.end>7200: raise ValueError('Duração máxima: 2 horas.')
            output.append(dict(start=segment.start,end=segment.end,text=segment.text))
            progress('transcribing',segment.end/max(info.duration,1)*100)
        finish_stage('asr_s')
        progress('finishing',99)
        # The model can output segment ends beyond the physical audio tail.
        # Do not falsify the duration or let the API reject the entire transcript.
        timing = reconcile_whisper_segments(output, decoded_duration)
        result=dict(**timing,language=info.language,duration=decoded_duration,
                    input_bytes=media.stat().st_size,model_revision=revisions.get(job['model']),title=media_title)
    except Exception as e:
        print(f'Inference error: {type(e).__name__}',file=sys.stderr,flush=True)
        result={'error':'Não foi possível transcrever. Verifique o arquivo, o acesso ao vídeo ou a configuração do executor.'}
    finally:
        done.set()
        thread.join(timeout=5)
        cpu=proc.cpu_times()
        elapsed=time.perf_counter()-started
        # Serialization is not measurable from the same final JSON payload without
        # a second rewrite. Keep it unavailable instead of reporting fake zero.
        recorded = {name:value for name,value in stages.items()}
        result.update(peaks,elapsed=elapsed,cpu_seconds=cpu.user+cpu.system,runtime=package_versions())
        result['metrics']=make_metrics(elapsed=elapsed,stages=recorded,
                    duration=result.get('duration') if not result.get('error') else None)
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')

if __name__=='__main__': run(sys.argv[1])
