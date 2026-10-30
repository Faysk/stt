"""Release safety regression tests: credentials and security headers.

All network calls are fake. No production jobs or secrets are used.
"""
import importlib
import json
import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient


def worker(tmp_path, monkeypatch):
    monkeypatch.setenv('TTS_WORKER_KEY','dummy-worker')
    monkeypatch.setenv('TTS_OUTBOX',str(tmp_path/'outbox'))
    monkeypatch.delenv('STT_WORKER_KEY',raising=False)
    monkeypatch.delenv('STT_OUTBOX',raising=False)
    with patch('dotenv.load_dotenv', lambda *args,**kwargs: None):
        import server.worker as module
        return importlib.reload(module)


def reply(code):
    return httpx.Response(code,json={'detail':'synthetic'},request=httpx.Request('POST','https://invalid.example/result'))


@pytest.mark.parametrize('status_code',[401,403])
def test_auth_denial_pauses_new_claims_without_dead_letter(tmp_path,monkeypatch,status_code):
    w=worker(tmp_path,monkeypatch)
    path=w._save_outbox({'id':'auth-test','lease':'synthetic','expires':time.time()+600},
                       {'text':'private test result'})
    calls=[]

    class Client:
        def post(self,*args,**kwargs):
            calls.append(1)
            return reply(status_code)

    monkeypatch.setattr(w,'client',Client())
    assert w._drain_outbox() is True
    data=json.loads(path.read_text(encoding='utf-8'))
    assert data['last_status']==status_code
    assert data['retry_at']>time.time()
    assert not data.get('dead_letter')
    assert data['payload']['text']=='private test result'
    assert w._drain_outbox() is True
    assert len(calls)==1


def test_auth_recovers_after_operator_corrects_credentials(tmp_path,monkeypatch):
    w=worker(tmp_path,monkeypatch)
    job={'id':'recover-auth','lease':'synthetic','expires':time.time()+600}
    path=w._save_outbox(job,{'text':'preserved'})
    attempts=[]

    class Client:
        def post(self,*args,**kwargs):
            attempts.append(1)
            if len(attempts)==1:
                return reply(403)
            return httpx.Response(200,json={'ok':True,'receipt':'c'*64},
                                  request=httpx.Request('POST','https://invalid.example/result'))

    monkeypatch.setattr(w,'client',Client())
    now=time.time()
    assert w._deliver(path,now)=='auth_blocked'
    assert path.exists()
    assert w._deliver(path,now+10)=='acked'
    assert len(attempts)==2
    assert not path.exists()


def test_permanent_unprocessable_payload_still_quarantines(tmp_path,monkeypatch):
    w=worker(tmp_path,monkeypatch)
    path=w._save_outbox({'id':'validation-error','lease':'synthetic',
                         'expires':time.time()+600},{'text':'bad payload'})

    class Client:
        def post(self,*args,**kwargs):
            return reply(422)

    monkeypatch.setattr(w,'client',Client())
    assert w._deliver(path)=='quarantined'
    data=json.loads(path.read_text())
    assert data['dead_letter_status']==422
    assert data['payload']['text']=='bad payload'


def test_early_api_denials_have_privacy_security_headers(tmp_path,monkeypatch):
    monkeypatch.setenv('TTS_DATA',str(tmp_path))
    monkeypatch.setenv('TTS_ADMIN_KEY','test-admin')
    monkeypatch.setenv('TTS_WORKER_KEY','test-worker')
    monkeypatch.setenv('TTS_REQUIRE_EDGE','0')
    monkeypatch.delenv('STT_DATA',raising=False)
    monkeypatch.delenv('STT_ADMIN_KEY',raising=False)
    monkeypatch.delenv('STT_WORKER_KEY',raising=False)
    monkeypatch.delenv('STT_REQUIRE_EDGE',raising=False)
    monkeypatch.delenv('STT_EDGE_KEY',raising=False)
    with patch('dotenv.load_dotenv',lambda *a,**kw:None):
        import server.app as module
        module=importlib.reload(module)
        with TestClient(module.app) as client:
            denied=client.post('/api/jobs/upload',headers={'x-session-key':'bad'},
                               content=b'no session')
            big=client.post('/api/jobs/upload',
                 headers={'x-session-key':'a'*64,'content-length':str(100*1024*1024)},
                 content=b'')
            monkeypatch.setenv('TTS_REQUIRE_EDGE','1')
            monkeypatch.setenv('TTS_EDGE_KEY','secret-edge')
            forbidden=client.get('/api/health')
        for res,code in [(denied,401),(big,413),(forbidden,403)]:
            assert res.status_code==code,res.text
            assert res.headers['cache-control']=='no-store'
            assert res.headers['x-content-type-options']=='nosniff'
            assert res.headers['referrer-policy']=='no-referrer'
            assert res.headers['x-robots-tag']=='noindex, nofollow, noarchive'
            assert "default-src 'self'" in res.headers['content-security-policy']


def test_streamed_body_without_content_length_returns_413(tmp_path,monkeypatch):
    """Transfer-Encoding: chunked cannot bypass the streaming upload budget."""
    monkeypatch.setenv('TTS_DATA',str(tmp_path))
    monkeypatch.setenv('TTS_ADMIN_KEY','test-admin')
    monkeypatch.setenv('TTS_WORKER_KEY','test-worker')
    monkeypatch.setenv('TTS_REQUIRE_EDGE','0')
    for suffix in ('DATA','ADMIN_KEY','WORKER_KEY','REQUIRE_EDGE'):
        monkeypatch.delenv('STT_'+suffix,raising=False)
    with patch('dotenv.load_dotenv',lambda *a,**kw:None):
        import server.app as module
        module=importlib.reload(module)
        monkeypatch.setattr(module,'MAX_BYTES',64)

        boundary='----stt-audit-fixture'
        def chunks():
            yield (f'--{boundary}\\r\\n'
                   'Content-Disposition: form-data; name="file"; filename="fixture.wav"\\r\\n'
                   'Content-Type: audio/wav\\r\\n\\r\\n').encode('ascii')
            yield b'0'*(1024*1024+128)
            yield (f'\\r\\n--{boundary}--\\r\\n').encode('ascii')

        with TestClient(module.app,raise_server_exceptions=False) as client:
            r=client.post('/api/jobs/upload',headers={
                            'x-session-key':'a'*64,
                            'content-type':f'multipart/form-data; boundary={boundary}'},
                          content=chunks())
        # Exact file format is intentionally invalid. The ASGI byte budget
        # must still terminate oversized streams before route validation.
        assert r.status_code==413,r.text
        assert r.headers['cache-control']=='no-store'


def test_qwen_exception_does_not_expose_private_paths(tmp_path,monkeypatch,capsys):
    """A model loader can include private paths or filenames in its error."""
    from server import qwen_engine
    job=tmp_path/'job.json'
    job.write_text(json.dumps({'model':'qwen-fast','source':'upload'}),encoding='utf-8')
    sensitive='C:/private/users/secrets/never-log-me'

    def reject(_):
        raise RuntimeError('model unreadable: '+sensitive)

    monkeypatch.setattr(qwen_engine,'model_directory',reject)
    qwen_engine.run(str(job))
    output=capsys.readouterr()
    result=json.loads((tmp_path/'result.json').read_text(encoding='utf-8'))
    assert 'RuntimeError' in output.err
    assert sensitive not in output.err
    assert sensitive not in json.dumps(result)
    assert result.get('error')
