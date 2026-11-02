"""Offline failure injection for the 2026-11-02 incident and audit."""
import importlib
import json
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import pytest

from server import workspace
from server.result_contract import Result, result_receipt


def worker(monkeypatch,tmp_path):
    monkeypatch.setenv('STT_WORKER_KEY','fixture-worker')
    monkeypatch.setenv('STT_OUTBOX',str(tmp_path/'outbox'))
    with patch('dotenv.load_dotenv',lambda *a,**kw:None):
        import server.worker as module
        return importlib.reload(module)


def test_workspace_permission_error_is_not_retried(tmp_path,monkeypatch):
    real=Path.mkdir
    attempts=[]
    def denied(path,*args,**kwargs):
        if path.name.startswith('stt-'):
            attempts.append(path)
            raise PermissionError('synthetic denied directory')
        return real(path,*args,**kwargs)
    monkeypatch.setattr(Path,'mkdir',denied)
    with pytest.raises(PermissionError):
        with workspace.directory(tmp_path):pytest.fail('inaccessible workspace entered')
    assert len(attempts)==1


def test_workspace_preflight_low_space_and_recovery(tmp_path,monkeypatch):
    from types import SimpleNamespace
    root=tmp_path/'tmp';outbox=tmp_path/'outbox'
    monkeypatch.setattr(workspace.shutil,'disk_usage',lambda _:SimpleNamespace(free=0))
    with pytest.raises(OSError,match='reserve'):workspace.preflight(root,outbox)
    monkeypatch.setattr(workspace.shutil,'disk_usage',lambda _:SimpleNamespace(free=workspace.MIN_FREE+1))
    assert workspace.preflight(root,outbox)==root
    assert list(root.iterdir())==[] and list(outbox.iterdir())==[]


@pytest.mark.parametrize('mode',['total','etag','range_too_large','empty','short_200'])
def test_input_rejects_inconsistent_or_truncated_representation(tmp_path,monkeypatch,mode):
    w=worker(monkeypatch,tmp_path)
    calls=[]
    def handle(request):
        calls.append(request)
        if mode=='empty':return httpx.Response(200,content=b'')
        if mode=='short_200':return httpx.Response(200,headers={'content-length':'9'},content=b'ab')
        if mode=='range_too_large':
            return httpx.Response(206,headers={'content-range':'bytes 0-1048576/1048577'},content=b'a')
        if len(calls)==1:
            return httpx.Response(206,headers={'content-range':'bytes 0-1/4','etag':'first'},content=b'ab')
        return httpx.Response(206,headers={'content-range':'bytes 2-3/'+('5' if mode=='total' else '4'),
                                         'etag':'changed' if mode=='etag' else 'first'},content=b'cd')
    monkeypatch.setattr(w,'client',httpx.Client(base_url='https://fixture.invalid',transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(w.time,'sleep',lambda _:None)
    with pytest.raises((RuntimeError,httpx.RemoteProtocolError)):
        w._download_input({'id':'fixture','lease':'fake'},tmp_path/'input',time.time()+60,lambda:None)
    assert len(calls)<=3


@pytest.mark.parametrize('status',[401,403,404,409,503,429])
def test_download_retries_only_transient_http_errors(tmp_path,monkeypatch,status):
    w=worker(monkeypatch,tmp_path);calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(status) if len(calls)==1 else httpx.Response(200,content=b'complete')
    monkeypatch.setattr(w,'client',httpx.Client(base_url='https://fixture.invalid',transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(w.time,'sleep',lambda _:None)
    if status in (503,429):
        w._download_input({'id':'fixture','lease':'fake'},tmp_path/'input',time.time()+60,lambda:None)
        assert (tmp_path/'input').read_bytes()==b'complete' and len(calls)==2
    else:
        with pytest.raises(httpx.HTTPStatusError):
            w._download_input({'id':'fixture','lease':'fake'},tmp_path/'input',time.time()+60,lambda:None)
        assert len(calls)==1


def test_receipt_mismatch_never_deletes_durable_result(tmp_path,monkeypatch):
    w=worker(monkeypatch,tmp_path)
    p=w._save_outbox({'id':'fixture','lease':'fake','expires':time.time()+600},{'text':'fixture'})
    monkeypatch.setattr(w,'client',httpx.Client(base_url='https://fixture.invalid',transport=httpx.MockTransport(
        lambda _:httpx.Response(200,json={'ok':True,'receipt':'f'*64}))))
    assert w._deliver(p)=='quarantined' and p.exists()
    assert json.loads(p.read_text())['dead_letter_reason']=='receipt_mismatch'


@pytest.mark.parametrize('heartbeat_status',[200,409])
def test_pending_delivery_renews_lease_and_ack_matches(tmp_path,monkeypatch,heartbeat_status):
    w=worker(monkeypatch,tmp_path);now=time.time()
    p=w._save_outbox({'id':'fixture','lease':'fake','expires':now+600},{'text':'fixture'})
    data=json.loads(p.read_text());data['delivery_heartbeat']=now-31
    w._atomic_json(p,data)
    calls=[]
    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/heartbeat'):return httpx.Response(heartbeat_status,json={'ok':True})
        return httpx.Response(200,json={'ok':True,'receipt':result_receipt(json.loads(request.content))})
    monkeypatch.setattr(w,'client',httpx.Client(base_url='https://fixture.invalid',transport=httpx.MockTransport(handle)))
    assert w._deliver(p,now)=='acked' and not p.exists()
    assert calls==['/api/worker/fixture/heartbeat','/api/worker/fixture/result']


def test_s7_quarantine_and_corrupt_spool_do_not_block_queue(tmp_path,monkeypatch):
    from s7 import worker as w
    cfg={'work':tmp_path}
    (tmp_path/'result-bad.json').write_text('{bad')
    q=tmp_path/'result-quarantine.json'
    q.write_text(json.dumps({'expires':time.time()+3600,'next_try':time.time()+3600,'quarantined_status':422}))
    assert w.drain(cfg)=='clear' and q.exists()


@pytest.mark.parametrize('payload',[
    {'lease':'fake','error':'fixture error'},
    {'lease':'fake','duration':4,'segments':[{'start':0,'end':4,'text':'Olá 中文 العربية'}],
     'result_state':'complete','runtime':{'engine':'fixture'}},
    {'lease':'fake','text':'untimed','timing':'none','duration':2,'elapsed':1.5},
])
def test_s7_receipt_matches_pydantic_api_contract(payload):
    from s7 import worker as w
    assert w.result_receipt(payload)==result_receipt(payload)


def test_runtime_metadata_budget_rejects_deep_and_oversized():
    with pytest.raises(ValueError):Result(lease='fake',runtime={'too_big':'x'*8001})
    value={}
    for _ in range(8):value={'nested':value}
    with pytest.raises(ValueError):Result(lease='fake',runtime=value)


@pytest.fixture
def api(tmp_path,monkeypatch):
    for name,value in {'STT_DATA':str(tmp_path),'STT_WORKER_KEY':'fixture-worker',
        'STT_ADMIN_KEY':'fixture-admin','STT_REQUIRE_EDGE':'0','STT_LIGHT_HOST':'0',
        'STT_S7_ENABLED':'0','STT_ADMIN_QR_ENABLED':'0','STT_ADMIN_LEGACY_ENABLED':'1'}.items():
        monkeypatch.setenv(name,value)
    with patch('dotenv.load_dotenv',lambda *a,**kw:None):
        import server.app as m
        m=importlib.reload(m)
        with TestClient(m.app) as c:yield c,m


def test_readiness_blocks_claim_and_online_but_control_recovers(api):
    c,m=api;auth={'x-worker-key':'fixture-worker'}
    assert c.post('/api/worker/control',headers=auth,json={'id':'notebook','ready':False,'reason':'WORKSPACE_UNAVAILABLE'}).status_code==200
    assert not c.get('/api/health').json()['online']
    assert c.post('/api/worker/claim',headers=auth,json={'id':'notebook','info':{}}).json()['ready'] is False
    c.post('/api/worker/control',headers=auth,json={'id':'notebook','ready':True})
    assert c.get('/api/health').json()['online']


@pytest.mark.parametrize('null_pulse,age',[(True,200),(False,7300)])
def test_coordinator_recovers_absent_heartbeat_and_exceeded_deadline(api,null_pulse,age):
    c,m=api;now=time.time();jid='a'*32
    with m.db() as connection:
        connection.execute("""INSERT INTO jobs(id,owner,status,created,expires,started,heartbeat,attempts)
            VALUES(?,?,'running',?,?,?,?,1)""",(jid,'fixture',now-8000,now+3600,now-age,None if null_pulse else now))
    m.maintenance()
    with m.db() as connection:
        assert connection.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0]=='queued'


def test_result_body_budget_and_depth_apply_before_parse(api):
    c,m=api;auth={'x-worker-key':'fixture-worker'}
    path='/api/worker/'+'a'*32+'/result'
    response=c.post(path,headers=auth,content=b'x'*(m.MAX_RESULT_BYTES+1))
    assert response.status_code==413
    nested=b'{"runtime":'+b'['*40+b'0'+b']'*40+b'}'
    assert c.post(path,headers=auth,content=nested).status_code==413
    def chunks():
        yield b'{"runtime":'
        yield b'['*40+b'0'+b']'*40+b'}'
    assert c.post(path,headers=auth,content=chunks()).status_code==413
