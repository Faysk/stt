"""One isolated inference process per job: releases model RAM/VRAM on exit."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

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
    try:
        media=folder/'input.media'
        if job['source']!='upload':
            import yt_dlp
            def limit(d):
                if d.get('downloaded_bytes',0)>90*1024**2:
                    raise ValueError('Download excede 90 MB.')
            with yt_dlp.YoutubeDL({'js_runtimes':{'node':{}},'format':'bestaudio/best','noplaylist':True,'outtmpl':str(media),'max_filesize':90*1024**2,'socket_timeout':30,'retries':2,'quiet':True,'progress_hooks':[limit],'match_filter':lambda info,**kw: 'Duração máxima: 2 horas.' if (info.get('duration') or 0)>7200 or info.get('is_live') else None}) as ydl:
                ydl.download([job['source']])
        # PyAV decodes independently of the uploaded filename, with a duration cap.
        import av
        with av.open(str(media)) as container:
            duration=(container.duration or 0)/av.time_base
            if duration>7200: raise ValueError('Duração máxima: 2 horas.')
        from faster_whisper import WhisperModel
        model=WhisperModel(job['model'],device=job['device'],compute_type=job['compute'],cpu_threads=8,download_root=os.getenv('TTS_MODELS',str(Path(__file__).resolve().parents[1]/'models')))
        segments,info=model.transcribe(str(media),beam_size=5,vad_filter=True)
        if info.duration>7200: raise ValueError('Duração máxima: 2 horas.')
        output=[]
        for segment in segments:
            if segment.end>7200: raise ValueError('Duração máxima: 2 horas.')
            output.append(dict(start=segment.start,end=segment.end,text=segment.text))
        result=dict(segments=output,language=info.language,duration=info.duration,input_bytes=media.stat().st_size)
    except Exception as e:
        print(f'Inference error: {type(e).__name__}',file=sys.stderr,flush=True)
        result={'error':'Não foi possível transcrever. Verifique o arquivo, o acesso ao vídeo ou a configuração do executor.'}
    finally:
        done.set()
        thread.join(timeout=5)
        cpu=proc.cpu_times()
        result.update(peaks,elapsed=time.perf_counter()-started,cpu_seconds=cpu.user+cpu.system)
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')

if __name__=='__main__': run(sys.argv[1])
