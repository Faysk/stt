import importlib
import io
import time

import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('TTS_DATA',str(tmp_path))
    monkeypatch.setenv('TTS_ADMIN_KEY','test-admin')
    monkeypatch.setenv('TTS_WORKER_KEY','test-worker')
    monkeypatch.setenv('TTS_REQUIRE_EDGE','0')
    import server.app as module
    m=importlib.reload(module)
    with TestClient(m.app) as c: yield c,m

SESSION={'x-session-key':'a'*64}
WORKER={'x-worker-key':'test-worker'}

def submit(c):
    r=c.post('/api/jobs/upload',headers=SESSION,files={'file':('voice.wav',io.BytesIO(b'not a real wave'),'audio/wav')})
    assert r.status_code==201,r.text
    return r.json()['id']

def test_session_isolation_and_exports(setup):
    c,m=setup
    jid=submit(c)
    assert c.get('/api/jobs').status_code==401
    assert c.get('/api/jobs',headers={'x-session-key':'b'*64}).json()==[]
    assert c.get(f'/api/jobs/{jid}',headers={'x-session-key':'b'*64}).status_code==404
    assert c.get('/api/admin').status_code==401
    claim=c.post('/api/worker/claim',headers=WORKER,json={'id':'notebook'}).json()['job']
    assert claim['id']==jid
    assert c.post('/api/worker/claim',headers=WORKER,json={'id':'notebook'}).json()['job'] is None
    payload={'lease':claim['lease'],'duration':5,'elapsed':2,'segments':[{'start':0,'end':5,'text':'Olá <script>mundo</script>'}],'language':'pt'}
    assert c.post(f'/api/worker/{jid}/result',headers=WORKER,json=payload).status_code==200
    assert c.post(f'/api/worker/{jid}/result',headers=WORKER,json=payload).status_code==409
    for fmt in ['txt','md','srt','json']:
        assert c.get(f'/api/jobs/{jid}/download/{fmt}',headers=SESSION).status_code==200
        assert c.get(f'/api/jobs/{jid}/download/{fmt}',headers={'x-session-key':'b'*64}).status_code==404
    assert '00:00:05,000' in c.get(f'/api/jobs/{jid}/download/srt',headers=SESSION).text
    with m.db() as db: db.execute('UPDATE jobs SET expires=? WHERE id=?',(time.time()-1,jid))
    assert c.get(f'/api/jobs/{jid}',headers=SESSION).status_code==404
    m.maintenance()
    assert not (m.BLOB/jid).exists()
    with m.db() as db:
        r=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        assert r['owner']=='' and r['source'] is None and r['title'] is None
        assert r['duration']==5

def test_validation_and_routing(setup):
    c,m=setup
    for url in ['http://127.0.0.1/a','https://youtube.com.evil.org/watch?v=abcdefghijk','https://youtube.com@127.0.0.1/watch?v=abcdefghijk','https://youtu.be/../../etc/passwd','https://youtube.com/playlist?list=foo','https://youtu.be:foo/abcdefghijk']:
        assert c.post('/api/jobs/youtube',headers=SESSION,json={'url':url}).status_code==400
    assert c.post('/api/jobs/upload',headers=SESSION,files={'file':('bad.exe',b'foo')}).status_code==400
    jid=submit(c)
    assert c.post('/api/worker/claim',headers=WORKER,json={'id':'other-pc'}).json()['job'] is None
    assert c.post('/api/worker/claim',json={'id':'notebook'}).status_code==401
    r=c.put('/api/admin/settings',headers={'x-admin-key':'test-admin'},json={'worker':'other-pc','model':'base','device':'cpu','compute':'int8','paused':False})
    assert r.status_code==200
    claim=c.post('/api/worker/claim',headers=WORKER,json={'id':'other-pc'}).json()['job']
    assert claim['id']==jid and claim['model']=='base'
    assert c.post(f'/api/worker/{jid}/heartbeat',headers=WORKER,json={'lease':'wrong'}).status_code==409
    with m.db() as db: db.execute('UPDATE jobs SET heartbeat=? WHERE id=?',(time.time()-181,jid))
    m.maintenance()
    assert c.get(f'/api/jobs/{jid}',headers=SESSION).json()['status']=='queued'
    assert c.post(f'/api/worker/{jid}/result',headers=WORKER,json={'lease':claim['lease']}).status_code==409

def test_queue_limits(setup):
    c,m=setup
    for _ in range(10): submit(c)
    assert c.post('/api/jobs/youtube',headers=SESSION,json={'url':'https://youtu.be/abcdefghijk'}).status_code==429
    assert c.post('/api/jobs/youtube',headers={'x-session-key':'b'*64},json={'url':'https://youtu.be/abcdefghijk'}).status_code==201

def test_edge_guard_and_size_limit(setup,monkeypatch):
    c,m=setup
    assert c.post('/api/jobs/upload',headers={**SESSION,'content-length':str(100*1024**2)},content=b'').status_code==413
    monkeypatch.setenv('TTS_REQUIRE_EDGE','1')
    monkeypatch.setenv('TTS_EDGE_KEY','edge-secret')
    assert c.get('/api/health').status_code==403
    assert c.get('/api/health',headers={'x-edge-key':'edge-secret'}).status_code==200

def test_parallel_claims(setup):
    from concurrent.futures import ThreadPoolExecutor
    c,m=setup
    submit(c)
    def attempt(_):
        return c.post('/api/worker/claim',headers=WORKER,json={'id':'notebook'}).json()['job']
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(attempt,range(20)))
    assert sum(job is not None for job in results)==1
