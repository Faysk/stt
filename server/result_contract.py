"""Shared result validation and canonical acknowledgement, without API startup."""
import hashlib
import json
from pydantic import BaseModel, Field, model_validator
from server.metrics import validate as validate_metrics

class Segment(BaseModel):
    start: float = Field(ge=0,le=7200,allow_inf_nan=False)
    end: float = Field(ge=0,le=7200,allow_inf_nan=False)
    text: str = Field(max_length=10000)

class Result(BaseModel):
    lease: str
    segments: list[Segment] = Field(default_factory=list,max_length=30000)
    language: str = Field(default='',max_length=20)
    duration: float = Field(default=0,ge=0,le=7200,allow_inf_nan=False)
    elapsed: float = Field(default=0,ge=0,allow_inf_nan=False)
    cpu_seconds: float = Field(default=0,ge=0,allow_inf_nan=False)
    peak_rss_mb: float = Field(default=0,ge=0,allow_inf_nan=False)
    peak_vram_mb: float = Field(default=0,ge=0,allow_inf_nan=False)
    input_bytes: int = Field(default=0,ge=0)
    error: str | None = Field(default=None,max_length=500)
    runtime: dict = Field(default_factory=dict)
    model_revision: str | None = Field(default=None,max_length=80)
    title: str | None = Field(default=None,max_length=180)
    result_state: str = Field(default='unknown',pattern='^(unknown|complete|silence|partial)$')
    metrics: dict | None = None
    text: str | None = Field(default=None,max_length=500000)
    timing: str = Field(default='segments',pattern='^(segments|none)$')

    @model_validator(mode='after')
    def chronology(self):
        stack=[(self.runtime,0)]
        entries=0
        while stack:
            item,depth=stack.pop()
            entries+=1
            if depth>6 or entries>256:
                raise ValueError('Runtime metadata exceeds structural budget')
            if isinstance(item,dict):
                if any(not isinstance(key,str) or len(key)>128 for key in item):
                    raise ValueError('Invalid runtime metadata key')
                stack.extend((value,depth+1) for value in item.values())
            elif isinstance(item,list):
                stack.extend((value,depth+1) for value in item)
        if len(json.dumps(self.runtime,ensure_ascii=False,allow_nan=False).encode('utf-8'))>8000:
            raise ValueError('Runtime metadata exceeds byte budget')
        if self.metrics is not None:
            validate_metrics(self.metrics)
            if abs(self.metrics['engine_elapsed_s']-self.elapsed)>0.5:
                raise ValueError('Tempo total e métricas de motor inconsistentes.')
        if self.timing == 'none' and self.segments:
            raise ValueError('Untimed text cannot contain timed segments.')
        if self.error and self.segments:
            raise ValueError('Erro não pode conter segmentos publicados.')
        previous = -1.0
        for segment in self.segments:
            if segment.end < segment.start:
                raise ValueError('Segmento com intervalo invertido.')
            if segment.start + 1e-6 < previous:
                raise ValueError('Segmentos fora da ordem cronológica.')
            if self.duration == 0 or segment.end > self.duration + 0.5:
                raise ValueError('Segmento além da duração declarada.')
            previous = segment.start  # legitimate overlapping speech is preserved
        if self.result_state == 'silence' and self.segments:
            raise ValueError('Silêncio não pode conter segmentos.')
        if self.result_state in ('complete','partial') and not self.segments and not (self.text or '').strip() and not self.error:
            raise ValueError('Resultado vazio exige estado silêncio ou desconhecido.')
        return self


def result_receipt(payload):
    value=payload if isinstance(payload,Result) else Result(**payload)
    canonical=json.dumps(value.model_dump(),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
