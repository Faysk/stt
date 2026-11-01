import importlib
import json
import time
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def lab(tmp_path,monkeypatch):
    for key,value in {'STT_DATA':str(tmp_path),'STT_ADMIN_KEY':'power-admin',
        'STT_WORKER_KEY':'power-worker','STT_S7_WORKER_KEY':'power-s7',
        'STT_REQUIRE_EDGE':'0','STT_LIGHT_HOST':'0','STT_S7_ENABLED':'1'}.items():
        monkeypatch.setenv(key,value)
    import server.app as module
    module=importlib.reload(module)
    with TestClient(module.app) as client:
        client.post('/api/worker/claim',headers=WORKER,json={'id':'notebook','info':{'compute_types':{'cpu':['int8']}}})
        yield module,client


ADMIN={'x-admin-key':'power-admin'}
WORKER={'x-worker-key':'power-worker'}


def test_disabled_worker_keeps_control_channel_but_cannot_claim_or_preload(lab):
    module,c=lab
    assert c.put('/api/admin/workers/notebook/power',json={'enabled':False}).status_code==401
    assert c.post('/api/worker/control',headers={'x-worker-key':'power-s7'},json={'id':'notebook'}).status_code==403
    assert c.put('/api/admin/workers/notebook/power',headers=ADMIN,json={'enabled':False}).status_code==200
    assert c.post('/api/worker/control',headers=WORKER,json={'id':'notebook','applied_enabled':False}).json()=={'enabled':False}
    assert c.post('/api/worker/claim',headers=WORKER,json={'id':'notebook','info':{}}).json()['job'] is None
    assert c.post('/api/worker/model-preloads/claim',headers=WORKER,json={'worker':'notebook'}).json()['operation'] is None
    assert c.post('/api/admin/model-preloads',headers=ADMIN,json={'worker':'notebook','model':'small'}).status_code==409
    assert not c.get('/api/health').json()['online']
    module.init()
    assert c.post('/api/worker/control',headers=WORKER,json={'id':'notebook'}).json()=={'enabled':False}
    assert c.put('/api/admin/workers/notebook/power',headers=ADMIN,json={'enabled':True}).status_code==200
    assert c.post('/api/worker/control',headers=WORKER,json={'id':'notebook','applied_enabled':True}).json()=={'enabled':True}


def test_power_off_fences_running_lease_and_returns_same_job_to_queue(lab):
    module,c=lab
    now=time.time()
    with module.db() as connection:
        connection.execute("INSERT INTO jobs(id,owner,title,source,status,created,expires,input_bytes) VALUES('power-job','owner','test.wav','upload','queued',?,?,1)",(now,now+3600))
    claim=c.post('/api/worker/claim',headers=WORKER,json={'id':'notebook','info':{}}).json()['job']
    assert claim is not None
    assert c.put('/api/admin/workers/notebook/power',headers=ADMIN,json={'enabled':False}).status_code==200
    heartbeat=c.post('/api/worker/power-job/heartbeat',headers=WORKER,json={'lease':claim['lease'],'stage':'transcribing'})
    assert heartbeat.status_code==409
    with module.db() as connection:
        row=connection.execute("SELECT * FROM jobs WHERE id='power-job'").fetchone()
        assert row['status']=='queued' and row['lease'] is None and row['worker'] is None
        assert row['owner']=='owner' and row['attempts']==1
    assert c.post('/api/admin/jobs/power-job/retry',headers=ADMIN).status_code==409


@pytest.mark.parametrize('status,result_state',[('failed','unknown'),('done','partial')])
def test_retry_preserves_owner_and_requires_unexpired_original(lab,status,result_state):
    module,c=lab
    now=time.time()
    with module.db() as connection:
        connection.execute("INSERT INTO jobs(id,owner,title,source,status,created,expires,input_bytes,result_state) VALUES('retry-job','owner','test.wav','upload',?,?,?,1,?)",(status,now,now+3600,result_state))
    assert c.post('/api/admin/jobs/retry-job/retry',headers=ADMIN).status_code==409
    folder=module.BLOB/'retry-job';folder.mkdir();(folder/'input.media').write_bytes(b'x')
    assert c.post('/api/admin/jobs/retry-job/retry',headers=ADMIN).status_code==200
    with module.db() as connection:
        row=connection.execute("SELECT * FROM jobs WHERE id='retry-job'").fetchone()
        assert row['status']=='queued' and row['owner']=='owner'
