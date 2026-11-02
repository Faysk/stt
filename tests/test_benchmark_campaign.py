import importlib.util
import json
from pathlib import Path

import pytest
from server.benchmarking import matrix,caption_agreement,caption_segments,CPU_TYPES,GPU_TYPES
from server.benchmarking import atomic_json


def module(name,filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'scripts'/filename)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def test_complete_matrix_is_66_unique_real_supported_combinations():
    plan=matrix({'cpu':CPU_TYPES,'cuda':GPU_TYPES})
    assert len(plan)==len({r['id'] for r in plan})==66
    assert sum(r['device']=='cpu' for r in plan)==24
    assert sum(r['device']=='cuda' for r in plan)==42
    assert all(r['trial']==1 for r in plan)
    restricted=matrix({'cpu':['int8'],'cuda':[]})
    assert len(restricted)==6 and all(r['compute']=='int8' for r in restricted)


def test_automatic_caption_disagreement_is_not_labeled_accuracy():
    out=caption_agreement('Olá mundo','Olá novo mundo')
    assert out['weak_wer']==.5 and out['reference_kind']=='youtube_automatic_unverified'
    assert 'accuracy' not in out
    assert caption_agreement('','anything') is None
    assert caption_segments({'events':[{'tStartMs':1000,'dDurationMs':2000,'segs':[{'utf8':'Olá'}]}]})==[dict(start=1,end=3,text='Olá')]


def test_gpu_board_energy_and_missing_samples_are_not_job_energy(tmp_path):
    trial=module('bench_trial_test','benchmark_trial.py')
    path=tmp_path/'gpu.csv'
    path.write_text('2026/11/02 10:00:00.000, 0, 100, 8188, 50, 10, 210\n'
                    '2026/11/02 10:00:01.000, 90, 500, 8188, 60, 40, 2200\n'
                    '2026/11/02 10:00:02.000, 90, 600, 8188, 62, 50, 2200\n')
    start=trial.datetime.datetime(2026,11,2,10,0,1).timestamp()
    result=trial.gpu_summary(path,start)
    assert result['gpu_board_energy_wh']==pytest.approx(45/3600)
    assert result['gpu_baseline_used_mb']==100
    assert result['gpu_host_used_mb_peak']==600
    assert result['cpu_energy_wh'] is None
    assert result['energy_scope']=='whole_gpu_board_including_other_processes'
    assert trial.gpu_summary(tmp_path/'absent',start)['gpu_board_energy_wh'] is None


def test_resume_ignores_instrument_pilot_and_keeps_actual_failures(tmp_path):
    campaign=module('bench_campaign_test','benchmark_campaign.py')
    row={'id':'cuda-tiny-float16','model':'tiny','device':'cuda','compute':'float16'}
    one=tmp_path/'runs'/row['id']/'attempt-001';one.mkdir(parents=True)
    (one/'result.json').write_text(json.dumps({'status':'complete'}))
    assert campaign.completed(tmp_path,row) is None
    two=one.parent/'attempt-002';two.mkdir()
    (two/'result.json').write_text(json.dumps({'status':'failed','instrumentation_version':2,'failure_kind':'out_of_memory'}))
    result=campaign.completed(tmp_path,row)
    assert result['status']=='failed' and result['failure_kind']=='out_of_memory'
    assert 'attempt-002' in result['artifact']


def test_resume_never_adopts_unrelated_process(monkeypatch,tmp_path):
    campaign=module('bench_campaign_identity_test','benchmark_campaign.py')
    class Process:
        def create_time(self):return 123
        def cmdline(self):return ['python','unrelated.py']
    import psutil
    monkeypatch.setattr(psutil,'Process',lambda _:Process())
    assert campaign.verified_process({'pid':1,'created':123},tmp_path/'job.json') is None


def test_atomic_progress_write_recovers_from_windows_reader_lock(tmp_path,monkeypatch):
    import server.benchmarking as b
    target=tmp_path/'progress.json';target.write_text('{}')
    actual=Path.replace;attempts=[]
    def replace(path,destination):
        attempts.append(1)
        if len(attempts)<3:raise PermissionError('synthetic reader sharing violation')
        return actual(path,destination)
    monkeypatch.setattr(Path,'replace',replace)
    monkeypatch.setattr(b.time,'sleep',lambda _:None)
    atomic_json(target,{'progress':50})
    assert json.loads(target.read_text())=={'progress':50} and len(attempts)==3
    assert not list(tmp_path.glob('*.new-*'))


def test_atomic_progress_write_does_not_retry_permanent_denial_forever(tmp_path,monkeypatch):
    import server.benchmarking as b
    attempts=[]
    def denied(*args):attempts.append(1);raise PermissionError('permanent')
    monkeypatch.setattr(Path,'replace',denied)
    monkeypatch.setattr(b.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError):atomic_json(tmp_path/'progress.json',{'progress':50})
    assert len(attempts)==12
