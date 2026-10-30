"""Model/metric contracts that can run without downloading weights or using CUDA."""
import json
from pathlib import Path

import pytest

from server.metrics import make_metrics,validate
from server.qwen_engine import RECIPES,model_directory,prepared_models,parse_decoded,EngineError
from scripts.benchmark_v2 import errors

def test_stage_metrics_preserve_missing_and_reuse_provenance():
    data=make_metrics(elapsed=9,stages={'download_s':2,'asr_s':6},duration=12)
    assert data['schema_version']==1 and data['alignment_s'] is None
    assert data['delivery_s'] is None and data['new_audio_seconds']==12
    assert data['reused_audio_seconds']==0 and data['cache_source']=='none'
    for changes in ({'asr_s':8}, {'download_s':float('nan')},
                    {'reused_audio_seconds':3}, {'cache_source':'untracked'}):
        with pytest.raises(ValueError):
            validate(dict(data,**changes))
    assert validate(dict(data,reused_audio_seconds=3,new_audio_seconds=9,
                         cache_source='checkpoint'))

def test_qwen_fixed_revision_and_confined_model_root(tmp_path):
    name='qwen-fast'
    item=RECIPES[name]
    path=tmp_path/item['directory']
    path.mkdir()
    for filename in ('config.json','processor_config.json','tokenizer.json','model.safetensors'):
        (path/filename).write_text('{}')
    marker=path/'model-revision.json'
    marker.write_text(json.dumps({'repository':item['repository'],'revision':'wrong'}))
    with pytest.raises(EngineError,match='QWEN_REVISION_MISMATCH'):
        model_directory(name,tmp_path)
    marker.write_text(json.dumps({'repository':item['repository'],'revision':item['revision']}))
    actual,recipe=model_directory(name,tmp_path)
    assert actual==path and recipe['revision']==item['revision']
    assert prepared_models(tmp_path)==[name]
    with pytest.raises(EngineError,match='QWEN_RECIPE_UNKNOWN'):
        model_directory('../../etc',tmp_path)

def test_qwen_parsed_output_must_be_structured():
    assert parse_decoded([{'transcription':' Portuguese text ','language':'Portuguese'}])==('Portuguese text','Portuguese')
    for value in ('bad',[],[{},{}],[{'language':'pt'}],[{'transcription':123}]):
        with pytest.raises(EngineError,match='QWEN_OUTPUT_INVALID'):
            parse_decoded(value)

def test_wer_cer_uses_human_reference_text():
    assert errors('Olá mundo','Olá mundo')['wer']==0
    assert errors('um dois','um três')['wer']==0.5
    assert errors('abc','abd')['cer']==pytest.approx(1/3)
