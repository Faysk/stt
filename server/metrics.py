"""Versioned, nullable per-attempt timing contract (seconds).

Missing readings are None, never fabricated zeros. Host telemetry is *not* job
energy or job-exclusive usage.
"""
import math

SCHEMA_VERSION = 1
STAGES = ('download_s','media_probe_s','model_load_s','asr_s','alignment_s','serialization_s','delivery_s')
TOLERANCE_SECONDS = 0.15

def make_metrics(*,elapsed,stages=None,duration=None,reused_audio_seconds=0,cache_source='none'):
    stages = stages or {}
    if set(stages) - set(STAGES):
        raise ValueError('Unknown metric stage')
    data = dict(schema_version=SCHEMA_VERSION,engine_elapsed_s=elapsed,
                new_audio_seconds=duration if duration is not None else None,
                reused_audio_seconds=reused_audio_seconds,
                cache_source=cache_source)
    data.update({name:stages.get(name) for name in STAGES})
    validate(data)
    return data

def validate(data):
    if not isinstance(data,dict) or data.get('schema_version')!=SCHEMA_VERSION:
        raise ValueError('Unsupported metrics schema')
    for name in (*STAGES,'engine_elapsed_s','new_audio_seconds','reused_audio_seconds'):
        value = data.get(name)
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or
                                  not math.isfinite(value) or value<0):
            raise ValueError('Invalid non-finite/negative metric: '+name)
    source=data.get('cache_source')
    if source not in ('none','checkpoint','result-cache','unknown'):
        raise ValueError('Unknown cache provenance')
    if source=='none' and (data.get('reused_audio_seconds') or 0)>0:
        raise ValueError('Reused work needs explicit provenance')
    total=data.get('engine_elapsed_s')
    known=sum(data.get(n) or 0 for n in STAGES if n!='delivery_s')
    if total is not None and known>total+TOLERANCE_SECONDS:
        raise ValueError('Stage time exceeds engine elapsed time')
    return data
