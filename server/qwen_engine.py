"""Opt-in Qwen3-ASR adapter. Runs ONLY under a separate Qwen Python environment.

No model downloads, silent CPU offloading or fabricated timestamps. Intended for
operator-controlled testing before making this engine public.
"""
from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

from server.metrics import make_metrics

RECIPES = {
    'qwen-fast': dict(repository='Qwen/Qwen3-ASR-0.6B-hf',
        revision='7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c',directory='qwen3-asr-0.6b-hf',
        min_available_gib=3.0),
    'qwen-quality': dict(repository='Qwen/Qwen3-ASR-1.7B-hf',
        revision='bcd2b5b7f32b480ab5790554cfa8347f246a14f3',directory='qwen3-asr-1.7b-hf',
        min_available_gib=6.0),
}
SAMPLE_RATE = 16_000
WINDOW_SECONDS = 60
MAX_TOKENS = 512
MAX_MEDIA_BYTES = 90 * 1024**2
MAX_AUDIO_SAMPLES = 7200 * SAMPLE_RATE

class EngineError(RuntimeError):
    pass

def model_directory(model, root=None):
    recipe=RECIPES.get(model)
    if not recipe: raise EngineError('QWEN_RECIPE_UNKNOWN')
    root=Path(root or os.getenv('TTS_QWEN_MODELS','models/qwen')).resolve()
    path=(root/recipe['directory']).resolve()
    if root not in path.parents:
        raise EngineError('QWEN_MODEL_PATH_INVALID')
    marker=path/'model-revision.json'
    try:
        data=json.loads(marker.read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:
        raise EngineError('QWEN_REVISION_MARKER_MISSING') from exc
    if data.get('repository') != recipe['repository'] or data.get('revision') != recipe['revision']:
        raise EngineError('QWEN_REVISION_MISMATCH')
    for name in ('config.json','processor_config.json','tokenizer.json'):
        if not (path/name).is_file():
            raise EngineError('QWEN_MODEL_FILE_MISSING')
    if not any(path.glob('*.safetensors')):
        raise EngineError('QWEN_WEIGHT_FILES_MISSING')
    return path,recipe

def prepared_models(root=None):
    ready=[]
    for profile in RECIPES:
        try:
            model_directory(profile,root)
        except EngineError:
            continue
        ready.append(profile)
    return ready

def iter_windows(path, av, np, seconds=WINDOW_SECONDS):
    """Stream mono float32 at 16kHz; never load entire recording."""
    limit=int(seconds*SAMPLE_RATE)
    chunks=[]
    size=0
    consumed=0
    with av.open(str(path)) as container:
        stream=next((s for s in container.streams if s.type=='audio'),None)
        if stream is None: raise EngineError('QWEN_NO_AUDIO_STREAM')
        resampler=av.AudioResampler(format='flt',layout='mono',rate=SAMPLE_RATE)
        def collect(frame):
            nonlocal size
            samples=frame.to_ndarray().reshape(-1).astype(np.float32,copy=False)
            # Decode metadata is untrusted. Reject overlong media before
            # handing another audio window to GPU inference.
            if consumed + size + int(samples.size) > MAX_AUDIO_SAMPLES:
                raise EngineError('QWEN_DURATION_EXCEEDED')
            if samples.size:
                chunks.append(samples.copy())
                size += int(samples.size)
        for frame in container.decode(stream):
            for item in resampler.resample(frame): collect(item)
            if size>=limit:
                joined=np.concatenate(chunks)
                start=0
                while len(joined)-start>=limit:
                    yield consumed/SAMPLE_RATE,joined[start:start+limit].copy()
                    consumed+=limit
                    start+=limit
                tail=joined[start:]
                chunks=[tail.copy()] if tail.size else []
                size=int(tail.size)
        for item in resampler.resample(None):collect(item)
        if size:
            yield consumed/SAMPLE_RATE,np.concatenate(chunks)
            consumed+=size
    if consumed<=0: raise EngineError('QWEN_AUDIO_EMPTY')
    if consumed>MAX_AUDIO_SAMPLES: raise EngineError('QWEN_DURATION_EXCEEDED')

def _near_digital_silence(np,audio):
    if not np.isfinite(audio).all() or audio.size==0:
        raise EngineError('QWEN_SIGNAL_INVALID')
    peak=float(np.max(np.abs(audio)))
    rms=float(np.sqrt(np.mean(np.square(audio,dtype=np.float64))))
    return (20*math.log10(max(peak,1e-6)) <= -84 and
            20*math.log10(max(rms,1e-6)) <= -90)

def parse_decoded(decoded):
    if not isinstance(decoded,(list,tuple)) or len(decoded)!=1 or not isinstance(decoded[0],dict):
        raise EngineError('QWEN_OUTPUT_INVALID')
    item=decoded[0]
    value=item.get('transcription')
    lang=item.get('language')
    if not isinstance(value,str) or (lang is not None and not isinstance(lang,str)):
        raise EngineError('QWEN_OUTPUT_INVALID')
    return value.strip(), (lang or '').strip()

def _gpu_gate(torch,minimum):
    if not torch.cuda.is_available():raise EngineError('QWEN_CUDA_UNAVAILABLE')
    cap=torch.cuda.get_device_capability(0)
    if cap < (7,5):raise EngineError('QWEN_COMPUTE_CAPABILITY_UNSUPPORTED')
    available,total=torch.cuda.mem_get_info(0)
    if available < minimum*1024**3:
        raise EngineError('QWEN_VRAM_INSUFFICIENT')
    return dict(capability=f'{cap[0]}.{cap[1]}',total_vram_bytes=total)

def _progress(folder,stage,percentage):
    file=folder/'progress.tmp'
    file.write_text(json.dumps({'stage':stage,'progress':min(99,max(0,percentage))}))
    file.replace(folder/'progress.json')

def run(config):
    folder=Path(config).resolve().parent
    job=json.loads(Path(config).read_text(encoding='utf-8'))
    start=time.perf_counter()
    stages={}
    output={}
    try:
        model_path,recipe=model_directory(job['model'])
        if job.get('device')!='cuda' or job.get('compute') not in ('bfloat16','float16'):
            raise EngineError('QWEN_GPU_RECIPE_REQUIRED')
        hint=job.get('language_hint','auto')
        if not isinstance(hint,str) or len(hint)>32 or not hint.replace('-','').isalpha():
            raise EngineError('QWEN_LANGUAGE_HINT_INVALID')
        import numpy as np
        import av
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor
        hardware=_gpu_gate(torch,recipe['min_available_gib'])
        dtype=getattr(torch,job['compute'])
        model=None
        processor=None
        _progress(folder,'loading',0)
        t=time.perf_counter()
        processor=AutoProcessor.from_pretrained(str(model_path),local_files_only=True)
        model=AutoModelForMultimodalLM.from_pretrained(
            str(model_path),dtype=dtype,device_map={'':'cuda:0'},local_files_only=True)
        device_map=getattr(model,'hf_device_map',{})
        if not str(model.device).startswith('cuda') or (
            device_map and any(str(v).startswith(('cpu','disk')) for v in device_map.values())):
            raise EngineError('QWEN_UNEXPECTED_OFFLOAD')
        stages['model_load_s']=time.perf_counter()-t
        media=folder/'input.media'
        if job['source']!='upload':
            import yt_dlp
            _progress(folder,'downloading',0)
            t=time.perf_counter()
            def limit_download(data):
                if data.get('downloaded_bytes',0) > MAX_MEDIA_BYTES:
                    raise EngineError('QWEN_DOWNLOAD_TOO_LARGE')
            with yt_dlp.YoutubeDL({'format':'bestaudio/best','noplaylist':True,
                    'outtmpl':str(media),'max_filesize':MAX_MEDIA_BYTES,'quiet':True,
                    'socket_timeout':30,'retries':2,'progress_hooks':[limit_download],
                    'match_filter':lambda info,**kwargs:
                    'Duration >2h or livestream' if (info.get('duration') or 0)>7200 or info.get('is_live') else None}) as ydl:
                ydl.extract_info(job['source'],download=True)
            if not media.is_file() or media.stat().st_size > MAX_MEDIA_BYTES:
                raise EngineError('QWEN_DOWNLOAD_TOO_LARGE')
            stages['download_s']=time.perf_counter()-t
        transcripts=[]
        languages=[]
        samples=0
        windows=0
        _progress(folder,'transcribing',0)
        t=time.perf_counter()
        for offset,audio in iter_windows(media,av,np):
            windows+=1
            samples+=len(audio)
            inputs=processor.apply_transcription_request(
                audio=audio,language=None if hint=='auto' else hint,prompt=None)
            inputs=inputs.to(model.device,model.dtype)
            with torch.inference_mode():
                ids=model.generate(**inputs,max_new_tokens=MAX_TOKENS)
            parsed=processor.decode(ids[:,inputs['input_ids'].shape[1]:],
                                    return_format='parsed')
            text,language=parse_decoded(parsed)
            if not text and not _near_digital_silence(np,audio):
                raise EngineError('QWEN_EMPTY_WITH_SIGNAL')
            if text: transcripts.append(text)
            if language: languages.append(language)
            _progress(folder,'transcribing',min(95,(offset+len(audio)/SAMPLE_RATE)/7200*100))
        stages['asr_s']=time.perf_counter()-t
        if not windows:raise EngineError('QWEN_AUDIO_EMPTY')
        transcript=' '.join(transcripts).strip()
        duration=samples/SAMPLE_RATE
        if duration>7200:raise EngineError('QWEN_DURATION_EXCEEDED')
        state='complete' if transcript else 'silence'
        _progress(folder,'finishing',99)
        output=dict(segments=[],text=transcript,timing='none',result_state=state,
                    duration=duration,language=languages[0] if languages and len(set(languages))==1 else 'mixed/auto',
                    input_bytes=media.stat().st_size,
                    model_revision=recipe['revision'],
                    runtime={'engine':'qwen3','model_id':recipe['repository'],
                             'revision':recipe['revision'],'compute':job['compute'],
                             'language_hint':hint,'timestamp_policy':'unavailable',
                             'torch':torch.__version__,
                             'transformers':__import__('transformers').__version__,
                             'hardware':hardware})
    except Exception as error:
        print(f'Qwen inference failed: {type(error).__name__}: {str(error)[:100]}',file=sys.stderr,flush=True)
        output={'error':'Não foi possível transcrever com o motor Qwen. Verifique o executor e o modelo local.'}
    finally:
        elapsed=time.perf_counter()-start
        output.update(elapsed=elapsed,metrics=make_metrics(elapsed=elapsed,stages=stages,
            duration=output.get('duration') if not output.get('error') else None))
        (folder/'result.json').write_text(json.dumps(output,ensure_ascii=False),encoding='utf-8')
        try:
            del model
            del processor
        except UnboundLocalError: pass
        gc.collect()

if __name__=='__main__':
    run(sys.argv[1])
