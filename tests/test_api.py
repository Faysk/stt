import importlib
import io
import time

import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('STT_DATA',str(tmp_path))
    monkeypatch.setenv('STT_ADMIN_KEY','test-admin')
    monkeypatch.setenv('STT_WORKER_KEY','test-worker')
    monkeypatch.setenv('STT_REQUIRE_EDGE','0')
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
    retry=c.post(f'/api/worker/{jid}/result',headers=WORKER,json=payload)
    assert retry.status_code==200 and retry.json()['duplicate'] is True
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
    monkeypatch.setenv('STT_REQUIRE_EDGE','1')
    monkeypatch.setenv('STT_EDGE_KEY','edge-secret')
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

ADMIN={'x-admin-key':'test-admin'}

def test_admin_priority_audit_and_anonymous_identity(setup):
    c,m=setup
    first=submit(c)
    second=submit(c)
    assert c.patch(f'/api/admin/jobs/{second}/priority',json={'priority':9}).status_code==401
    assert c.patch(f'/api/admin/jobs/{second}/priority',headers=ADMIN,json={'priority':10}).status_code==422
    assert c.patch(f'/api/admin/jobs/{second}/priority',headers=ADMIN,json={'priority':9}).status_code==200
    listed=c.get('/api/jobs',headers=SESSION).json()
    assert next(j for j in listed if j['id']==second)['position']==1
    assert next(j for j in listed if j['id']==first)['position']==2
    assert all('worker' not in j and 'model' not in j for j in listed)
    claimed=c.post('/api/worker/claim',headers=WORKER,json={'id':'notebook'}).json()['job']
    assert claimed['id']==second
    assert c.patch(f'/api/admin/jobs/{second}/priority',headers=ADMIN,json={'priority':1}).status_code==409
    assert c.post(f'/api/worker/{second}/heartbeat',headers=WORKER,json={'lease':claimed['lease'],'stage':'transcribing','progress':35}).status_code==200
    result={'lease':claimed['lease'],'duration':10,'elapsed':2,'runtime':{'faster-whisper':'1.2.1'},'model_revision':'a'*40}
    assert c.post(f'/api/worker/{second}/result',headers=WORKER,json=result).status_code==200
    detail=c.get(f'/api/admin/jobs/{second}',headers=ADMIN).json()
    assert [e['kind'] for e in detail['events']]==['submitted','priority_changed','started','stage','done']
    assert all(k not in detail['job'] for k in ['owner','lease','source'])
    label=detail['job']['client_tag']
    assert len(label)==16 and label!=m.hashlib.sha256(('a'*64).encode()).hexdigest()[:16]
    assert detail['job']['model_revision']=='a'*40
    assert c.get('/api/admin/jobs',headers=ADMIN,params={'search':label}).json()['total']==2
    with m.db() as d: d.execute('UPDATE jobs SET expires=? WHERE id=?',(time.time()-1,second))
    m.maintenance()
    detail=c.get(f'/api/admin/jobs/{second}',headers=ADMIN).json()['job']
    assert detail['title'] is None and detail['outcome']=='done' and detail['client_tag']==label
    usage=c.get('/api/admin/clients',headers=ADMIN).json()
    assert usage['clients'][0]['jobs']==2
    assert usage['clients'][0]['audio_seconds']==10

def test_history_preserves_peaks_gaps_and_validates_period(setup):
    c,m=setup
    now=time.time()
    with m.db() as d:
        d.execute('DELETE FROM telemetry')
        sample=int(now//60)*60-120
        m.obs.record(d,'api-host',10,40,None,20,1,now=sample+10)
        m.obs.record(d,'api-host',90,60,70,20,5,now=sample+11)
        history=m.obs.history(d,'hour',now=now)
    point=next(p for p in history['points'] if p['n']==2)
    assert point['cpu']==50 and point['cpu_max']==90 and point['queue_max']==5
    assert point['gpu']==70 and point['gpu_max']==70
    assert any(p['cpu'] is None and p['submitted']==0 for p in history['points'])
    for period in m.obs.PERIODS:
        response=c.get('/api/admin/history',headers=ADMIN,params={'period':period})
        assert response.status_code==200
        assert len(response.json()['points'])<=200
    assert c.get('/api/admin/history',headers=ADMIN,params={'period':'invalid'}).status_code==400
    assert c.get('/api/admin/history').status_code==401

def test_filters_and_public_status(setup):
    c,m=setup
    ids=[submit(c) for _ in range(4)]
    h=c.get('/api/health').json()
    assert h['pressure']=='medium' and h['waiting']==4
    assert c.get('/api/admin/jobs',headers=ADMIN,params={'status':'running'}).json()['total']==0
    assert c.get('/api/admin/jobs',headers=ADMIN,params={'search':ids[0][:10]}).json()['total']==1
    assert c.get('/api/admin/jobs',headers=ADMIN,params={'search':"' OR 1=1 --"}).json()['total']==0
    assert c.get('/api/admin/jobs',headers=ADMIN,params={'page':0}).status_code==400
    assert c.get('/api/admin/jobs',headers=ADMIN,params={'limit':100000}).status_code==400
    assert c.get('/api/admin/jobs',headers=ADMIN,params={'status':'bad'}).status_code==400
    assert len(c.get('/api/admin',headers=ADMIN).json()['catalog'])==6

def test_migration_is_idempotent(setup):
    c,m=setup
    jid=submit(c)
    with m.db() as d:
        d.execute("UPDATE jobs SET status='done',outcome=NULL,client_tag=NULL WHERE id=?",(jid,))
    m.init()
    m.init()
    with m.db() as d:
        row=d.execute('SELECT outcome,client_tag FROM jobs WHERE id=?',(jid,)).fetchone()
    assert row['outcome']=='done' and row['client_tag']
