"""Deterministic regressions for the 2026-10-30 reliability audit.

No external network, real media, GPU or production service is used.
"""
import importlib
import io
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

SESSION = {'x-session-key': 'a' * 64}
WORKER = {'x-worker-key': 'test-worker'}

@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv('TTS_DATA',str(tmp_path))
    monkeypatch.setenv('TTS_ADMIN_KEY','test-admin')
    monkeypatch.setenv('TTS_WORKER_KEY','test-worker')
    monkeypatch.setenv('TTS_REQUIRE_EDGE','0')
    monkeypatch.setenv('TTS_OUTBOX',str(tmp_path/'outbox'))
    with patch('dotenv.load_dotenv',lambda *args,**kwargs:None):
        import server.app as module
        module=importlib.reload(module)
        with TestClient(module.app) as client:
            yield client,module

def submit(c):
    response=c.post('/api/jobs/upload',headers=SESSION,
        files={'file':('fixture.wav',io.BytesIO(b'fixture'),'audio/wav')})
    assert response.status_code==201,response.text
    return response.json()['id']

def claim(c):
    r=c.post('/api/worker/claim',headers=WORKER,json={'id':'notebook'})
    assert r.status_code==200,r.text
    return r.json()['job']

def test_admission_precedes_multipart_spooling(lab,monkeypatch):
    c,m=lab
    key=m.hashlib.sha256(b'localtestclient').hexdigest()
    with m.db() as d:
        d.executemany('INSERT INTO admission VALUES (?,?)',[(key,time.time())]*20)
    from starlette.datastructures import UploadFile
    written=[]
    original=UploadFile.write
    async def spy(self,data):
        written.append(len(data))
        return await original(self,data)
    monkeypatch.setattr(UploadFile,'write',spy)
    response=c.post('/api/jobs/upload',headers=SESSION,
       files={'file':('denied.wav',b'x'*(2*1024*1024),'audio/wav')})
    assert response.status_code==429
    assert written==[]
    with m.db() as d:
        assert d.execute('SELECT count(*) FROM upload_slots').fetchone()[0]==0

def test_upload_reservations_bound_inflight_and_clean_up(lab):
    c,m=lab
    with m.db() as d:
        for n in range(4):
            d.execute('INSERT INTO upload_slots VALUES (?,?,?)',(str(n),'some-owner',time.time()))
    response=c.post('/api/jobs/upload',headers=SESSION,
                    files={'file':('blocked.wav',b'hello','audio/wav')})
    assert response.status_code==429
    with m.db() as d:
        d.execute('DELETE FROM upload_slots')
    assert submit(c)
    with m.db() as d:
        assert d.execute('SELECT count(*) FROM upload_slots').fetchone()[0]==0

def test_expired_media_purge_retries_and_is_private(lab,monkeypatch):
    c,m=lab
    jid=submit(c)
    folder=m.BLOB/jid
    with m.db() as d: d.execute('UPDATE jobs SET expires=? WHERE id=?',(time.time()-1,jid))
    original=m.shutil.rmtree
    failures=[]
    def fail_once(path,*args,**kwargs):
        if Path(path)==folder and not failures:
            failures.append(True)
            raise PermissionError('locked fixture')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(m.shutil,'rmtree',fail_once)
    m.maintenance()
    assert folder.exists()
    assert c.get('/api/jobs/'+jid,headers=SESSION).status_code==404
    with m.db() as d:
        row=d.execute('SELECT status,purged_at,purge_attempts,purge_error FROM jobs WHERE id=?',(jid,)).fetchone()
        assert row['status']=='expired' and row['purged_at'] is None
        assert row['purge_attempts']==1 and row['purge_error']=='PermissionError'
        d.execute('UPDATE jobs SET purge_retry_at=0 WHERE id=?',(jid,))
    m.maintenance()
    with m.db() as d:
        row=d.execute('SELECT purged_at,purge_error FROM jobs WHERE id=?',(jid,)).fetchone()
        assert row['purged_at'] is not None and row['purge_error'] is None
    assert not folder.exists()
    m.maintenance()  # No double-deletion.

def test_invalid_timestamps_are_never_published(lab):
    c,m=lab
    jid=submit(c)
    lease=claim(c)['lease']
    for segments,duration in [
        ([{'start':8,'end':2,'text':'invalid'}],4),
        ([{'start':4,'end':5,'text':'outside'}],2),
        ([{'start':2,'end':3,'text':'second'},{'start':1,'end':2,'text':'first'}],5),
        ([{'start':0,'end':1,'text':'no duration'}],0),
    ]:
        result=c.post(f'/api/worker/{jid}/result',headers=WORKER,
                      json={'lease':lease,'duration':duration,'segments':segments})
        assert result.status_code==422,result.text
    assert c.get(f'/api/jobs/{jid}/download/srt',headers=SESSION).status_code==404
    ok=c.post(f'/api/worker/{jid}/result',headers=WORKER,
            json={'lease':lease,'duration':5,'result_state':'complete',
                  'segments':[{'start':0,'end':3,'text':'one'},
                              {'start':2,'end':4,'text':'overlap'}]})
    assert ok.status_code==200,ok.text
    data=c.get(f'/api/jobs/{jid}/download/json',headers=SESSION).json()
    assert data['schema_version']==1 and data['result_state']=='complete'

def test_result_receipts_are_idempotent_and_fenced(lab):
    c,m=lab
    jid=submit(c)
    lease=claim(c)['lease']
    payload={'lease':lease,'duration':2,'segments':[{'start':0,'end':2,'text':'receipt'}]}
    first=c.post(f'/api/worker/{jid}/result',headers=WORKER,json=payload)
    second=c.post(f'/api/worker/{jid}/result',headers=WORKER,json=payload)
    assert first.status_code==200 and second.status_code==200
    assert second.json()=={'ok':True,'receipt':first.json()['receipt'],'duplicate':True}
    altered=dict(payload,duration=3)
    assert c.post(f'/api/worker/{jid}/result',headers=WORKER,json=altered).status_code==409
    with m.db() as d: d.execute('UPDATE jobs SET expires=? WHERE id=?',(time.time()-1,jid))
    assert c.post(f'/api/worker/{jid}/result',headers=WORKER,json=payload).status_code==409

def test_stale_heartbeat_does_not_modify_new_attempt(lab):
    c,m=lab
    jid=submit(c)
    old=claim(c)
    with m.db() as d: d.execute('UPDATE jobs SET heartbeat=? WHERE id=?',(time.time()-181,jid))
    m.maintenance()
    new=claim(c)
    assert new['lease']!=old['lease']
    denied=c.post(f'/api/worker/{jid}/heartbeat',headers=WORKER,
          json={'lease':old['lease'],'stage':'transcribing','progress':83})
    assert denied.status_code==409
    with m.db() as d:
        row=d.execute('SELECT lease,stage,progress FROM jobs WHERE id=?',(jid,)).fetchone()
    assert row['lease']==new['lease'] and row['stage']=='preparing' and row['progress']==0

def worker_module():
    with patch('dotenv.load_dotenv',lambda *args,**kwargs:None):
        import server.worker as module
        return importlib.reload(module)

def test_durable_outbox_survives_transient_timeout_and_ack_loss(lab,monkeypatch):
    _,m=lab
    w=worker_module()
    now=time.time()
    job={'id':'audit-fixture','lease':'lease-1','expires':now+500}
    path=w._save_outbox(job,{'duration':2,'segments':[{'start':0,'end':1,'text':'valid'}]})
    calls=[]
    class Sender:
        def post(self,path,**kwargs):
            calls.append(kwargs['json'].copy())
            if len(calls)==1: raise httpx.ReadTimeout('fixture')
            return httpx.Response(200,json={'ok':True,'receipt':'a'*64,'duplicate':True},
                                 request=httpx.Request('POST','http://example.invalid'+path))
    monkeypatch.setattr(w,'client',Sender())
    assert w._deliver(path,now)=='pending'
    assert path.exists()
    assert 'error' not in json.loads(path.read_text())['payload']
    assert w._deliver(path,now+10)=='acked'
    assert not path.exists()
    assert calls[0]==calls[1]

def test_worker_interrupt_cleans_up_child(lab,monkeypatch):
    _,m=lab
    w=worker_module()
    children=[]
    class Fake:
        stopped=False
        def __init__(self,*args,**kwargs): children.append(self)
        def poll(self): raise KeyboardInterrupt('synthetic interruption')
        def terminate(self): self.stopped=True
        def kill(self): self.stopped=True
        def wait(self,timeout=None):return 0
    monkeypatch.setattr(w.subprocess,'Popen',Fake)
    with pytest.raises(KeyboardInterrupt):
        w.execute({'id':'interrupt','lease':'lease','source':'https://youtu.be/abcdefghijk','expires':time.time()+60})
    assert children and children[0].stopped

def test_expired_download_never_spawns_inference(lab,monkeypatch):
    _,m=lab
    w=worker_module()
    clock=[1000.0]
    created=[]
    class Stream:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def raise_for_status(self):pass
        def iter_bytes(self):
            yield b'chunk'
            clock[0]=1003.0
            yield b'late'
    class Sender:
        def stream(self,*args,**kwargs):return Stream()
        def post(self,*args,**kwargs):
            return httpx.Response(200,json={'ok':True,'receipt':'a'*64},
                                  request=httpx.Request('POST','http://example.invalid'))
    monkeypatch.setattr(w,'client',Sender())
    monkeypatch.setattr(w.time,'time',lambda:clock[0])
    monkeypatch.setattr(w.subprocess,'Popen',lambda *a,**kw:created.append(a))
    w.execute({'id':'expired','lease':'lease','source':'upload','expires':1001.0})
    assert not created


def test_same_session_slots_count_before_body(lab):
    c,m=lab
    # Nine accepted jobs and one in-flight upload fill this session's quota.
    for _ in range(9): submit(c)
    owner=m.hashlib.sha256(SESSION['x-session-key'].encode()).hexdigest()
    with m.db() as d:
        d.execute('INSERT INTO upload_slots VALUES (?,?,?)',('reserved',owner,time.time()))
    result=c.post('/api/jobs/upload',headers=SESSION,
                  files={'file':('denied.wav',b'x'*4096,'audio/wav')})
    assert result.status_code==429

def test_untimed_result_does_not_export_fake_srt(lab):
    c,m=lab
    jid=submit(c)
    lease=claim(c)['lease']
    result=c.post(f'/api/worker/{jid}/result',headers=WORKER,
                  json={'lease':lease,'duration':3,'timing':'none',
                        'result_state':'complete','text':'Texto sem alinhamento.'})
    assert result.status_code==200,result.text
    public=c.get('/api/jobs',headers=SESSION).json()[0]
    assert public['timing']=='none'
    assert c.get(f'/api/jobs/{jid}/download/srt',headers=SESSION).status_code==404
    assert c.get(f'/api/jobs/{jid}/download/txt',headers=SESSION).text=='Texto sem alinhamento.'
    structured=c.get(f'/api/jobs/{jid}/download/json',headers=SESSION).json()
    assert structured['timing']=='none' and structured['segments']==[]
    assert structured['text']=='Texto sem alinhamento.'

def test_metrics_persist_separately_from_queue_time(lab):
    c,m=lab
    jid=submit(c)
    lease=claim(c)['lease']
    stages={'schema_version':1,'engine_elapsed_s':4,'new_audio_seconds':3,
            'reused_audio_seconds':0,'cache_source':'none','asr_s':3.5}
    payload={'lease':lease,'duration':3,'elapsed':4,'metrics':stages}
    ok=c.post(f'/api/worker/{jid}/result',headers=WORKER,json=payload)
    assert ok.status_code==200,ok.text
    detail=c.get(f'/api/admin/jobs/{jid}',headers={'x-admin-key':'test-admin'}).json()['job']
    assert detail['metrics']['asr_s']==3.5 and detail['queue_seconds'] is not None
    assert detail['job_total_seconds']>=detail['queue_seconds']
    jid2=submit(c)
    lease2=claim(c)['lease']
    bad=dict(payload,lease=lease2,metrics=dict(stages,engine_elapsed_s=7))
    assert c.post(f'/api/worker/{jid2}/result',headers=WORKER,json=bad).status_code==422

def test_qwen_configuration_requires_worker_capability(lab):
    c,m=lab
    jid=submit(c)
    requested={'worker':'notebook','model':'qwen-fast','device':'cuda',
               'compute':'bfloat16','engine':'qwen3','language_hint':'auto',
               'paused':False}
    res=c.put('/api/admin/settings',headers={'x-admin-key':'test-admin'},json=requested)
    assert res.status_code==200,res.text
    not_ready=c.post('/api/worker/claim',headers=WORKER,json={'id':'notebook','info':{'qwen_models':[]}})
    assert not_ready.status_code==200 and not_ready.json()['job'] is None
    ready=c.post('/api/worker/claim',headers=WORKER,json={'id':'notebook',
                  'info':{'qwen_models':['qwen-fast']}})
    job=ready.json()['job']
    assert job['id']==jid and job['engine']=='qwen3' and job['language_hint']=='auto'


def test_heartbeat_and_requeue_are_serialized(lab,monkeypatch):
    c,m=lab
    jid=submit(c)
    current=claim(c)
    with m.db() as d:
        d.execute('UPDATE jobs SET heartbeat=? WHERE id=?',(time.time()-181,jid))
    entered=threading.Event()
    resume=threading.Event()
    maintenance_done=threading.Event()
    original=m.leased
    results=[]
    def paused(c,key,lease):
        row=original(c,key,lease)
        if threading.current_thread().name=='heartbeat-race':
            entered.set()
            assert resume.wait(5)
        return row
    monkeypatch.setattr(m,'leased',paused)
    heartbeat_thread=threading.Thread(name='heartbeat-race',
        target=lambda:results.append(m.heartbeat(jid,m.Heartbeat(
            lease=current['lease'],stage='transcribing',progress=83))))
    hb_error=[]
    def maintenance_thread():
        try: m.maintenance()
        except Exception as exc: hb_error.append(exc)
        finally: maintenance_done.set()
    heartbeat_thread.start()
    assert entered.wait(5)
    janitor=threading.Thread(target=maintenance_thread)
    janitor.start()
    try:
        # While heartbeat holds BEGIN IMMEDIATE, maintenance cannot take
        # an inconsistent snapshot and requeue the old lease.
        assert not maintenance_done.wait(0.1)
    finally:
        resume.set()
        heartbeat_thread.join(5)
        janitor.join(5)
    assert not hb_error and maintenance_done.is_set()
    assert results==[{'ok':True}]
    with m.db() as d:
        row=d.execute('SELECT status,lease,stage,progress FROM jobs WHERE id=?',(jid,)).fetchone()
    assert row['status']=='running' and row['lease']==current['lease']
    assert row['stage']=='transcribing' and row['progress']==83

def test_purge_does_not_follow_foreign_symlink(lab,tmp_path):
    c,m=lab
    jid=submit(c)
    folder=m.BLOB/jid
    import shutil
    shutil.rmtree(folder)
    foreign=tmp_path/'sentinel'
    foreign.mkdir()
    (foreign/'keep.txt').write_text('private')
    folder.symlink_to(foreign,target_is_directory=True)
    with m.db() as d:
        d.execute('UPDATE jobs SET expires=? WHERE id=?',(time.time()-1,jid))
    m.maintenance()
    assert (foreign/'keep.txt').read_text()=='private'
    assert c.get('/api/jobs/'+jid,headers=SESSION).status_code==404
    with m.db() as d:
        row=d.execute('SELECT purged_at,purge_error FROM jobs WHERE id=?',(jid,)).fetchone()
    assert row['purged_at'] is None and row['purge_error']=='OSError'
