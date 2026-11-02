"""Offline campaign primitives. No API startup or credentials in artifacts."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import unicodedata


MODELS=('tiny','base','small','medium','large-v3','turbo')
CPU_TYPES=('int8','int8_float32','int16','float32')
GPU_TYPES=('float16','int8_float16','int8','int8_bfloat16','bfloat16','int8_float32','float32')


def atomic_json(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.new-'+str(os.getpid()))
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.flush();os.fsync(stream.fileno())
    # Windows readers/antivirus can briefly open without FILE_SHARE_DELETE.
    # Bound retries; a progress-reader race must not invalidate the inference.
    try:
        for attempt in range(12):
            try:temporary.replace(path);break
            except PermissionError:
                if attempt==11:raise
                time.sleep(min(.25,.01*2**attempt))
    finally:
        temporary.unlink(missing_ok=True)


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024**2),b''):digest.update(block)
    return digest.hexdigest()


def matrix(supported):
    rows=[]
    # GPU first yields useful results while the long CPU cohort is still running.
    for device,types in (('cuda',GPU_TYPES),('cpu',CPU_TYPES)):
        for model in MODELS:
            for compute in types:
                if compute not in supported.get(device,[]):continue
                rows.append(dict(id=f'{device}-{model}-{compute}',device=device,
                                 model=model,compute=compute,trial=1))
    return rows


def words(text):
    return re.findall(r'\w+',unicodedata.normalize('NFKC',text).casefold())


def caption_agreement(reference,hypothesis):
    """Disagreement with *unverified automatic* captions, never true accuracy."""
    from rapidfuzz.distance import Levenshtein
    a,b=words(reference),words(hypothesis)
    if not a:return None
    ac,bc=' '.join(a),' '.join(b)
    return dict(reference_kind='youtube_automatic_unverified',
                weak_wer=Levenshtein.distance(a,b)/len(a),
                weak_cer=Levenshtein.distance(ac,bc)/max(1,len(ac)),
                reference_words=len(a),hypothesis_words=len(b))


def caption_segments(document):
    out=[]
    for event in document.get('events',[]):
        text=''.join(piece.get('utf8','') for piece in event.get('segs',[])).strip()
        if not text:continue
        out.append(dict(start=event.get('tStartMs',0)/1000,
                        end=(event.get('tStartMs',0)+event.get('dDurationMs',0))/1000,text=text))
    return out
