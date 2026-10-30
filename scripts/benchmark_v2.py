"""Reproducible model-to-model comparison; no transcript contents in reports.

Manifest: {"samples":[{"id":"pt1","media":"media/pt1.wav",
"reference":"references/pt1.txt","language":"pt"}]}.
Media/reference paths resolve relative to the manifest, never committed by this tool.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

# Direct invocation via "python scripts/benchmark_v2.py" needs the project root.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.qwen_engine import RECIPES

def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC',text).casefold().split())

def distance(a,b):
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        row=[i]
        for j,y in enumerate(b,1):
            row.append(min(row[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=row
    return previous[-1]

def errors(reference,hypothesis):
    a=re.findall(r'\w+',normalize(reference))
    b=re.findall(r'\w+',normalize(hypothesis))
    chars_a=list(normalize(reference))
    chars_b=list(normalize(hypothesis))
    return {'wer':distance(a,b)/max(1,len(a)),
            'cer':distance(chars_a,chars_b)/max(1,len(chars_a)),
            'reference_words':len(a)}

def manifest_samples(manifest):
    obj=json.loads(manifest.read_text(encoding='utf-8'))
    samples=obj.get('samples')
    if not isinstance(samples,list) or not samples:
        raise ValueError('Dataset requires a nonempty samples list')
    for item in samples:
        if not all(isinstance(item.get(k),str) and item[k] for k in ('id','media','reference','language')):
            raise ValueError('Dataset sample requires id/media/reference/language')
        def confined(key):
            root=manifest.parent.resolve()
            candidate=(root/item[key]).resolve()
            if root not in candidate.parents or not candidate.is_file():
                raise ValueError('Missing/outside dataset file')
            return candidate
        yield item['id'],item['language'],confined('media'),confined('reference')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('dataset',type=Path)
    p.add_argument('--engine',choices=['whisper','qwen3'],default='whisper')
    p.add_argument('--model',default='small')
    p.add_argument('--device',choices=['cpu','cuda'],default='cpu')
    p.add_argument('--compute',default='int8')
    p.add_argument('--language-hint',default='auto')
    p.add_argument('--seed',type=int,default=0)
    p.add_argument('--repeat',type=int,default=3)
    p.add_argument('--out',type=Path,default=Path('logs/benchmark-v2.json'))
    args=p.parse_args()
    if not (1<=args.repeat<=20): p.error('repeat must be between 1 and 20')
    if args.engine=='qwen3' and args.model not in RECIPES:
        p.error('unknown Qwen recipe')
    runtime=sys.executable if args.engine=='whisper' else os.getenv('STT_QWEN_PYTHON','')
    if not runtime or not Path(runtime).is_file() or (
       args.engine=='qwen3' and Path(runtime).resolve()==Path(sys.executable).resolve()):
        p.error('Qwen requires an independent STT_QWEN_PYTHON executable')
    root=Path(__file__).resolve().parents[1]
    samples=list(manifest_samples(args.dataset))
    output=[]
    for id,language,media,reference in samples:
        expected=reference.read_text(encoding='utf-8')
        media_sha=hashlib.sha256(media.read_bytes()).hexdigest()
        for repeat in range(args.repeat):
            with tempfile.TemporaryDirectory(prefix='benchmark-v2-') as folder:
                working=Path(folder)
                shutil.copyfile(media,working/'input.media')
                config=dict(engine=args.engine,model=args.model,source='upload',device=args.device,
                    compute=args.compute,language_hint=args.language_hint,seed=args.seed)
                (working/'job.json').write_text(json.dumps(config),encoding='utf-8')
                cmd=[runtime,'-m','server.infer' if args.engine=='whisper' else 'server.qwen_engine',
                     str(working/'job.json')]
                start=time.perf_counter()
                try:
                    subprocess.run(cmd,cwd=root,timeout=7200,check=True)
                    result=json.loads((working/'result.json').read_text(encoding='utf-8'))
                    if result.get('error'): raise RuntimeError('ENGINE_REPORTED_ERROR')
                    transcript=(result.get('text') or ' '.join(s['text'] for s in result.get('segments',[])))
                    row={**errors(expected,transcript),
                        'status':'complete','result_state':result.get('result_state','unknown'),
                        'metrics':result.get('metrics'),
                        'duration_s':result.get('duration'),
                        'runtime':result.get('runtime'),
                        'model_revision':result.get('model_revision'),
                        'peak_rss_mb':result.get('peak_rss_mb'),
                        'peak_vram_mb':result.get('peak_vram_mb')}
                except (subprocess.TimeoutExpired,subprocess.CalledProcessError,OSError,ValueError,RuntimeError):
                    row={'status':'failed','metrics':None}
                row.update(sample_id=id,language=language,media_sha256=media_sha,engine=args.engine,
                    model=args.model,device=args.device,compute=args.compute,trial=repeat+1,
                    seed=args.seed,wall_s=round(time.perf_counter()-start,4),
                    cache_source='none',fresh_trial=True)
                output.append(row)
                args.out.parent.mkdir(parents=True,exist_ok=True)
                args.out.write_text(json.dumps({'schema_version':2,'results':output},
                         ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({k:row.get(k) for k in ('sample_id','trial','status','wer','cer','wall_s')}),flush=True)
    return 1 if any(row['status']=='failed' for row in output) else 0

if __name__=='__main__':raise SystemExit(main())
