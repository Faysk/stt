import importlib
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest
from server import hosting


def test_missing_android_cpu_sensor_preserves_ram_history():
    from server import observability
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory=sqlite3.Row
        connection.execute('CREATE TABLE jobs(created REAL,outcome TEXT,status TEXT,duration REAL,elapsed REAL)')
        observability.install(connection)
        observability.record(connection,'api-host',None,50,None,20,0,now=1000)
        points=observability.history(connection,'minute',now=1000)['points']
        sampled=[point for point in points if point['ram'] is not None]
        assert len(sampled)==1 and sampled[0]['ram']==50 and sampled[0]['cpu'] is None


def test_restricted_mode_tracks_primary_availability_and_explicit_s7_selection(monkeypatch):
    monkeypatch.setenv('STT_LIGHT_HOST', '1')
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript('CREATE TABLE settings(id,value); CREATE TABLE workers(id,seen);')
        connection.execute('INSERT INTO settings VALUES(1,?)', (json.dumps({'worker':'notebook'}),))
        assert hosting.capabilities(connection, 100)['limited']
        connection.execute('INSERT INTO workers VALUES(?,?)', ('notebook',99))
        assert hosting.capabilities(connection, 100)['youtube']
        assert hosting.capabilities(connection, 190)['max_mb'] == 8
        connection.execute('UPDATE settings SET value=?', (json.dumps({'worker':'s7-edge'}),))
        assert hosting.capabilities(connection, 100)['limited']
        monkeypatch.setenv('STT_LIGHT_HOST','0')
        assert not hosting.capabilities(connection,100)['limited']


@pytest.fixture
def limited_api(tmp_path,monkeypatch):
    for name,value in {'STT_DATA':str(tmp_path),'STT_REQUIRE_EDGE':'0',
                       'STT_ADMIN_KEY':'hosting-test','STT_WORKER_KEY':'worker-test',
                       'STT_LIGHT_HOST':'1'}.items():
        monkeypatch.setenv(name,value)
    import server.app as app
    module=importlib.reload(app)
    # The Windows test volume is not Android SD. Test admission separately.
    monkeypatch.setattr(hosting,'storage_guard',lambda *a,**k:None)
    with TestClient(module.app) as client:
        yield module,client


def test_limited_mode_rejects_youtube_before_body_and_unsupported_upload(limited_api):
    module,client=limited_api
    headers={'x-session-key':'a'*64}
    assert client.get('/api/health').json()['formats']==['wav','mp3','ogg','m4a']
    assert client.post('/api/jobs/youtube',headers=headers,content=b'invalid json').status_code==503
    assert client.post('/api/jobs/upload',headers=headers,files={'file':('video.mp4',b'video')}).status_code==400
    with module.db() as connection:
        assert connection.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
        assert connection.execute('SELECT count(*) FROM upload_slots').fetchone()[0]==0


@pytest.mark.parametrize('extension',['ogg','m4a'])
def test_long_ogg_is_rejected_before_queue_and_small_ogg_is_eligible(limited_api,monkeypatch,extension):
    module,client=limited_api
    monkeypatch.setattr(hosting,'duration_probe',lambda *a:301)
    headers={'x-session-key':'b'*64}
    assert client.post('/api/jobs/upload',headers=headers,files={'file':('voice.'+extension,b'fixture')}).status_code==413
    monkeypatch.setattr(hosting,'duration_probe',lambda *a:30)
    response=client.post('/api/jobs/upload',headers=headers,files={'file':('voice.'+extension,b'fixture')})
    assert response.status_code==201
    with module.db() as connection:
        job=connection.execute('SELECT * FROM jobs WHERE id=?',(response.json()['id'],)).fetchone()
        assert job['duration']==30 and module.s7_eligible(job)
        assert connection.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
