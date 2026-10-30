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


def test_real_api_result_can_be_restored_for_offline_legacy_rollback(tmp_path,monkeypatch):
    """Integration test against the actual SQLite schema and result renderer.

    This deliberately uses synthetic media and a synthetic result, without
    running a model, downloading weights or touching a production database.
    """
    from scripts.restore_legacy_exports import main as restore
    monkeypatch.setenv('TTS_DATA',str(tmp_path))
    monkeypatch.setenv('TTS_ADMIN_KEY','test-admin')
    monkeypatch.setenv('TTS_WORKER_KEY','test-worker')
    monkeypatch.setenv('TTS_REQUIRE_EDGE','0')
    for suffix in ('DATA','ADMIN_KEY','WORKER_KEY','REQUIRE_EDGE'):
        monkeypatch.delenv('STT_'+suffix,raising=False)
    session={'x-session-key':'a'*64}
    worker_header={'x-worker-key':'test-worker'}
    with patch('dotenv.load_dotenv',lambda *a,**kw:None):
        import server.app as module
        module=importlib.reload(module)
        with TestClient(module.app) as client:
            upload=client.post('/api/jobs/upload',headers=session,
                files={'file':('fixture.wav',b'synthetic fixture','audio/wav')})
            assert upload.status_code==201,upload.text
            jid=upload.json()['id']
            claimed=client.post('/api/worker/claim',headers=worker_header,
                json={'id':'notebook'}).json()['job']
            assert claimed['id']==jid
            result=client.post(f'/api/worker/{jid}/result',headers=worker_header,
                json={'lease':claimed['lease'],'duration':2,
                      'result_state':'complete',
                      'segments':[{'start':0,'end':2,'text':'integration fixture'}]})
            assert result.status_code==200,result.text
        flat=module.BLOB/jid/'transcript.txt'
        assert not flat.exists()
        assert restore(['--data-root',str(tmp_path)])==0
        assert not flat.exists()
        assert restore(['--data-root',str(tmp_path),
                       '--apply','--ack-offline-and-backed-up'])==0
        assert flat.read_text(encoding='utf-8')=='integration fixture'
        assert (module.BLOB/jid/'transcript.srt').is_file()
        # Generations remain readable by modern APIs after flat restoration.
        with TestClient(module.app) as client:
            assert client.get(f'/api/jobs/{jid}/download/txt',headers=session).text=='integration fixture'
            assert client.get(f'/api/jobs/{jid}/download/srt',headers=session).status_code==200


def test_outbox_atomic_rename_fsyncs_directory_on_posix(tmp_path,monkeypatch):
    """A successful payload must survive crash between rename and directory flush."""
    import os
    import stat
    if os.name=='nt':
        pytest.skip('Directory fsync is unavailable on Windows')
    w=worker(tmp_path,monkeypatch)
    actual_fsync=os.fsync
    flushed=[]
    def observe(fd):
        flushed.append('dir' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
        return actual_fsync(fd)
    monkeypatch.setattr(w.os,'fsync',observe)
    path=w.OUTBOX/'durable.json'
    w._atomic_json(path,{'payload':'synthetic-transcript'})
    assert json.loads(path.read_text(encoding='utf-8'))['payload']=='synthetic-transcript'
    assert flushed==['file','dir']


def test_new_generation_fsyncs_parent_directory_before_sqlite_can_point_to_it(tmp_path,monkeypatch):
    import server.app as mod
    folder=tmp_path/'job-data'
    folder.mkdir()
    observed=[]
    actual=mod._fsync_directory
    def observe(path):
        observed.append(path)
        return actual(path)
    monkeypatch.setattr(mod,'_fsync_directory',observe)
    digest='a'*64
    mod._publish_generation(folder,digest,{'txt':'test result'})
    assert (folder/'results'/digest/'transcript.txt').read_text(encoding='utf-8')=='test result'
    assert observed[0]==folder
    assert observed[1].parent==folder/'results'
    assert observed[1].name.startswith('.stage-')
    assert observed[2]==folder/'results'


def test_generation_rejects_symlinked_results_directory(tmp_path):
    import server.app as mod
    folder=tmp_path/'job'
    folder.mkdir()
    outside=tmp_path/'outside'
    outside.mkdir()
    (folder/'results').symlink_to(outside,target_is_directory=True)
    with pytest.raises(OSError,match='Unsafe result storage directory'):
        mod._publish_generation(folder,'a'*64,{'txt':'must not escape'})
    assert list(outside.iterdir())==[]


@pytest.mark.parametrize('source',['upload','youtube'])
def test_failed_workspace_creation_does_not_publish_unprocessable_job(
        source,tmp_path,monkeypatch):
    """A filesystem failure must never leave an unservable queued job."""
    monkeypatch.setenv('TTS_DATA',str(tmp_path))
    monkeypatch.setenv('TTS_ADMIN_KEY','test-admin')
    monkeypatch.setenv('TTS_WORKER_KEY','test-worker')
    monkeypatch.setenv('TTS_REQUIRE_EDGE','0')
    for suffix in ('DATA','ADMIN_KEY','WORKER_KEY','REQUIRE_EDGE'):
        monkeypatch.delenv('STT_'+suffix,raising=False)
    with patch('dotenv.load_dotenv',lambda *a,**kw:None):
        import server.app as module
        module=importlib.reload(module)
        with TestClient(module.app) as client:
            original=module.Path.mkdir
            def no_workspace(path,*args,**kwargs):
                if path.parent==module.BLOB and path.name!='.tmp':
                    raise OSError('synthetic disk failure')
                return original(path,*args,**kwargs)
            monkeypatch.setattr(module.Path,'mkdir',no_workspace)
            with pytest.raises(OSError,match='synthetic disk failure'):
                if source=='upload':
                    client.post('/api/jobs/upload',
                                headers={'x-session-key':'a'*64},
                                files={'file':('fixture.wav',b'dummy','audio/wav')})
                else:
                    client.post('/api/jobs/youtube',
                                headers={'x-session-key':'a'*64},
                                json={'url':'https://www.youtube.com/watch?v=dQw4w9WgXcQ'})
            with module.db() as conn:
                assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
            assert list(module.BLOB.glob('[0-9a-f]'*32))==[]
