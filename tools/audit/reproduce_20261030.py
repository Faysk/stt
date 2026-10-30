"""Audit-only probes: PASS confirms the audited behavior, not a regression fix.
No live service, GPU inference, private files or external requests are used.
"""
import importlib
import io
import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

@pytest.fixture
def lab(tmp_path,monkeypatch):
    for key,value in {'TTS_DATA':str(tmp_path),'TTS_ADMIN_KEY':'audit-admin','TTS_WORKER_KEY':'audit-worker','TTS_EDGE_KEY':'audit-edge','TTS_REQUIRE_EDGE':'0','TTS_URL':'http://example.invalid','TTS_TMP':str(tmp_path)}.items():
        monkeypatch.setenv(key,value)
    with patch('dotenv.load_dotenv',lambda *a,**k:False):
        import server.app as module
        module=importlib.reload(module)
        module.init()
    return TestClient(module.app),module

SESSION={'x-session-key':'a'*64}
WORKER={'x-worker-key':'audit-worker'}
def submit(c):
    r=c.post('/api/jobs/upload',headers=SESSION,files={'file':('audit.wav',b'audit-only','audio/wav')})
    assert r.status_code==201,r.text
    return r.json()['id']
def claim(c):
    r=c.post('/api/worker/claim',headers=WORKER,json={'id':'notebook'})
    assert r.status_code==200,r.text
    return r.json()['job']

def test_A01_rate_limited_upload_is_spooled_before_rejection(lab,monkeypatch):
    c,m=lab
    network=m.hashlib.sha256(b'audit-edgetestclient').hexdigest()
    with m.db() as d:
        d.executemany('INSERT INTO admission VALUES(?,?)',[(network,time.time())]*20)
    from starlette.datastructures import UploadFile
    original=UploadFile.write
    written=[]
    async def spy(self,data):
        written.append(len(data))
        return await original(self,data)
    monkeypatch.setattr(UploadFile,'write',spy)
    r=c.post('/api/jobs/upload',headers=SESSION,files={'file':('denied.wav',b'x'*(2*1024*1024),'audio/wav')})
    assert r.status_code==429
    assert sum(written)==2*1024*1024
    print('A01: HTTP 429, but 2097152 bytes reached multipart spooling first.')

def test_A02_failed_retention_cleanup_is_never_retried(lab,monkeypatch):
    c,m=lab;jid=submit(c);folder=m.BLOB/jid
    with m.db() as d:d.execute('UPDATE jobs SET expires=? WHERE id=?',(time.time()-1,jid))
    with monkeypatch.context() as scoped:
        scoped.setattr(m.shutil,'rmtree',lambda *args,**kwargs:None)
        m.maintenance()
    assert folder.exists()
    m.maintenance()
    with m.db() as d:status=d.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0]
    assert status=='expired' and folder.exists()
    assert c.get('/api/jobs/'+jid,headers=SESSION).status_code==404
    print('A02: Access expired, but media remains after a second healthy cleanup pass.')

def test_A03_invalid_segment_timestamps_are_published(lab):
    c,m=lab;jid=submit(c);j=claim(c)
    r=c.post('/api/worker/'+jid+'/result',headers=WORKER,json={'lease':j['lease'],'duration':4,'segments':[{'start':8,'end':2,'text':'audit fixture'}]})
    assert r.status_code==200
    out=c.get('/api/jobs/'+jid+'/download/srt',headers=SESSION)
    assert '00:00:08,000 --> 00:00:02,000' in out.text
    print('A03: Reversed interval beyond declared duration accepted and exported as SRT.')

def worker_module(monkeypatch):
    with patch('dotenv.load_dotenv',lambda *a,**k:False):
        import server.worker as worker
        return importlib.reload(worker)

class FakeClient:
    def __init__(self,fail_first=False):self.calls=[];self.fail_first=fail_first
    def post(self,path,**kwargs):
        self.calls.append(kwargs['json'].copy())
        if self.fail_first and len(self.calls)==1:raise httpx.ReadTimeout('audit injected timeout')
        return httpx.Response(200,json={'ok':True},request=httpx.Request('POST','http://example.invalid'+path))


def test_A04_valid_result_is_deleted_after_transient_delivery_failure(lab,monkeypatch):
    _,m=lab;w=worker_module(monkeypatch);fake=FakeClient(fail_first=True);monkeypatch.setattr(w,'client',fake);paths=[]
    class Done:
        def __init__(self,args,**kwargs):
            folder=Path(args[-1]).parent;paths.append(folder)
            (folder/'result.json').write_text(json.dumps({'duration':1,'segments':[{'start':0,'end':1,'text':'completed audit fixture'}]}))
        def poll(self):return 0
    monkeypatch.setattr(w.subprocess,'Popen',Done)
    w.execute({'id':'audit','lease':'audit-lease','source':'https://youtu.be/abcdefghijk','expires':time.time()+600})
    assert 'segments' in fake.calls[0] and 'error' in fake.calls[1]
    assert not paths[0].exists()
    print('A04: One upload timeout converts success to failure and removes the only local result.')

def test_A05_interrupt_leaves_inference_child_unterminated(lab,monkeypatch):
    _,m=lab;w=worker_module(monkeypatch);fake=FakeClient();monkeypatch.setattr(w,'client',fake);instances=[]
    class Running:
        killed=False
        def __init__(self,*a,**kw):instances.append(self)
        def poll(self):raise KeyboardInterrupt('audit-only synthetic cancellation')
        def kill(self):self.killed=True
        def terminate(self):self.killed=True
    monkeypatch.setattr(w.subprocess,'Popen',Running)
    with pytest.raises(KeyboardInterrupt):
        w.execute({'id':'audit','lease':'audit-lease','source':'https://youtu.be/abcdefghijk','expires':time.time()+600})
    assert not instances[0].killed
    print('A05: Worker finally stops heartbeat but never terminates the inference child on interruption.')

def test_A06_old_heartbeat_mutates_new_lease(lab,monkeypatch):
    c,m=lab;jid=submit(c);old=claim(c);entered=threading.Event();resume=threading.Event();original=m.leased;out=[]
    def paused(d,key,lease):
        result=original(d,key,lease)
        if threading.current_thread().name=='audit-stale-heartbeat':
            entered.set();assert resume.wait(10)
        return result
    monkeypatch.setattr(m,'leased',paused)
    thread=threading.Thread(name='audit-stale-heartbeat',target=lambda:out.append(m.heartbeat(jid,m.Heartbeat(lease=old['lease'],stage='transcribing',progress=83))))
    thread.start();assert entered.wait(10)
    try:
        with m.db() as d:d.execute('UPDATE jobs SET heartbeat=? WHERE id=?',(time.time()-181,jid))
        m.maintenance();new=claim(c);assert new['lease']!=old['lease']
    finally:resume.set();thread.join(10)
    assert out==[{'ok':True}]
    with m.db() as d:r=d.execute('SELECT lease,stage,progress FROM jobs WHERE id=?',(jid,)).fetchone()
    assert r['lease']==new['lease'] and r['stage']=='transcribing' and r['progress']==83
    print('A06: Stale heartbeat returned 200 and changed progress/stage of a newer attempt.')

def test_A07_expired_download_still_spawns_inference(lab,monkeypatch):
    _,m=lab;w=worker_module(monkeypatch);clock=[1000.0];spawned=[]
    class Stream:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_bytes(self):
            yield b'first';clock[0]=1003.0;yield b'second'
    fake=FakeClient();fake.stream=lambda *a,**kw:Stream()
    monkeypatch.setattr(w,'client',fake);monkeypatch.setattr(w.time,'time',lambda:clock[0])
    class Done:
        def __init__(self,args,**kw):
            spawned.append(clock[0]);(Path(args[-1]).parent/'result.json').write_text('{"segments":[]}')
        def poll(self):return 0
    monkeypatch.setattr(w.subprocess,'Popen',Done)
    w.execute({'id':'audit','lease':'audit-lease','source':'upload','expires':1001.0})
    assert spawned==[1003.0]
    print('A07: Input stream passed expiry, yet inference was still launched afterward.')
