"""Synthetic streaming media probes: never load a GPU model for bad input."""
import json
import sys
import types

import pytest

from server import qwen_engine as qwen


class Frame:
    def __init__(self, samples, rate=16000):
        self.samples=samples
        self.sample_rate=rate


class Stream:
    type='audio'
    codec_context=types.SimpleNamespace(sample_rate=16000)


class Container:
    def __init__(self, frames, with_audio=True):
        self.frames=frames
        self.streams=[Stream()] if with_audio else []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def decode(self, stream):
        assert stream is self.streams[0]
        yield from self.frames


def fake_av(frames, with_audio=True):
    return types.SimpleNamespace(open=lambda _:Container(frames,with_audio))


def test_qwen_probe_rejects_over_two_hours_before_inference():
    av=fake_av([Frame(3600*16000),Frame(3601*16000)])
    with pytest.raises(qwen.EngineError,match='QWEN_DURATION_EXCEEDED'):
        qwen.probe_audio_duration('synthetic.wav',av)
    assert qwen.probe_audio_duration('synthetic.wav',
        fake_av([Frame(60*16000),Frame(20*16000)]))==80


def test_qwen_probe_rejects_empty_corrupt_and_missing_audio():
    with pytest.raises(qwen.EngineError,match='QWEN_NO_AUDIO_STREAM'):
        qwen.probe_audio_duration('fake',fake_av([],False))
    with pytest.raises(qwen.EngineError,match='QWEN_AUDIO_EMPTY'):
        qwen.probe_audio_duration('fake',fake_av([]))
    with pytest.raises(qwen.EngineError,match='QWEN_AUDIO_FRAME_INVALID'):
        qwen.probe_audio_duration('fake',fake_av([Frame(0)]))
    with pytest.raises(qwen.EngineError,match='QWEN_AUDIO_FRAME_INVALID'):
        qwen.probe_audio_duration('fake',fake_av([Frame(-1,0)]))


def test_bad_media_does_not_touch_torch_or_gpu_before_preprobe(tmp_path,monkeypatch):
    job={'source':'upload','model':'qwen-fast','device':'cuda',
         'compute':'bfloat16','language_hint':'auto'}
    (tmp_path/'job.json').write_text(json.dumps(job),encoding='utf-8')
    (tmp_path/'input.media').write_bytes(b'fake-media')
    monkeypatch.setattr(qwen,'model_directory',
                        lambda model:(tmp_path,{'min_available_gib':3.0}))
    monkeypatch.setitem(sys.modules,'av',fake_av([Frame(7201*16000)]))
    torch_touched=[]
    class Torch(types.ModuleType):
        def __getattr__(self,name):
            torch_touched.append(name)
            raise AssertionError('GPU must not be initialized for invalid media')
    monkeypatch.setitem(sys.modules,'torch',Torch('torch'))
    qwen.run(str(tmp_path/'job.json'))
    result=json.loads((tmp_path/'result.json').read_text(encoding='utf-8'))
    assert result['error']
    assert torch_touched==[]
    assert result['metrics']['model_load_s'] is None
