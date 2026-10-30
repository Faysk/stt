"""Regression tests for HTTP 422: fail the job explicitly, without GPU retries.

All media/transcript content is synthetic, with temporary database/outbox.
"""
import json
import tempfile
import time

import httpx
from fastapi.testclient import TestClient


def test_invalid_result_can_be_failed_immediately_without_reclaim(tmp_path, monkeypatch):
    monkeypatch.setenv('STT_WORKER_KEY', 'test-secret')
    from server import app as service

    monkeypatch.setattr(service, 'DB', tmp_path / 'db' / 'stt.sqlite3')
    monkeypatch.setattr(service, 'BLOB', tmp_path / 'blob')
    old_tmpdir = tempfile.tempdir
    try:
        service.init()
        job_id = 'c' * 32
        now = time.time()
        with service.db() as c:
            c.execute(
                """INSERT INTO jobs
                (id,owner,title,source,status,created,expires,worker,lease,
                 heartbeat,attempts,stage)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id,'synthetic-user',None,'upload','running',now-10,
                 now+3600,'notebook','synthetic-lease',now,1,'transcribing'))

        client = TestClient(service.app)
        headers = {'x-worker-key':'test-secret'}
        invalid = client.post(
            f'/api/worker/{job_id}/result', headers=headers,
            json={'lease':'synthetic-lease',
                  'segments':[{'start':0,'end':12,'text':'Synthetic text'}],
                  'duration':10, 'result_state':'complete'})
        assert invalid.status_code == 422
        with service.db() as c:
            assert c.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()[0] == 'running'

        unauthenticated = client.post(
            f'/api/worker/{job_id}/reject',
            json={'lease':'synthetic-lease','reason':'result_schema_rejected'})
        assert unauthenticated.status_code == 401
        wrong_reason = client.post(
            f'/api/worker/{job_id}/reject', headers=headers,
            json={'lease':'synthetic-lease','reason':'arbitrary'})
        assert wrong_reason.status_code == 422
        wrong_lease = client.post(
            f'/api/worker/{job_id}/reject', headers=headers,
            json={'lease':'other','reason':'result_schema_rejected'})
        assert wrong_lease.status_code == 409

        accepted = client.post(
            f'/api/worker/{job_id}/reject', headers=headers,
            json={'lease':'synthetic-lease','reason':'result_schema_rejected'})
        assert accepted.status_code == 200
        assert accepted.json() == {'ok':True}
        with service.db() as c:
            job = c.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            assert job['status'] == job['outcome'] == job['stage'] == 'failed'
            assert job['attempts'] == 1
            assert job['lease'] is None
            assert job['finished'] is not None
            assert job['error'] == 'Resultado do executor inválido; entrega rejeitada pela API.'
            events = c.execute(
                'SELECT kind,data FROM events WHERE job=? ORDER BY id',(job_id,)).fetchall()
            assert [(event['kind'],json.loads(event['data'])) for event in events] == [
                ('failed', {'reason':'result_schema_rejected'})]

        repeated = client.post(
            f'/api/worker/{job_id}/reject', headers=headers,
            json={'lease':'synthetic-lease','reason':'result_schema_rejected'})
        assert repeated.status_code == 409
    finally:
        tempfile.tempdir = old_tmpdir


def test_worker_quarantines_422_and_sends_minimal_failure_notice(tmp_path, monkeypatch):
    monkeypatch.setenv('STT_WORKER_KEY', 'test-secret')
    from server import worker
    monkeypatch.setattr(worker, 'OUTBOX', tmp_path / 'outbox')
    events = []

    class Client:
        def post(self, endpoint, **kwargs):
            events.append((endpoint, kwargs['json']))
            if endpoint.endswith('/result'):
                return httpx.Response(422, json={'detail':'invalid result'},
                    request=httpx.Request('POST','https://localhost'+endpoint))
            if endpoint.endswith('/reject'):
                return httpx.Response(200,json={'ok':True},
                    request=httpx.Request('POST','https://localhost'+endpoint))
            raise AssertionError('Unexpected endpoint')

    monkeypatch.setattr(worker, 'client', Client())
    job_id = 'd' * 32
    job = {'id':job_id,'lease':'synthetic-lease',
           'expires':time.time()+3600}
    path = worker._save_outbox(job, {'segments':[{'start':0,'end':7,'text':'synthetic'}],
                                      'duration':5,'result_state':'complete'})
    assert worker._deliver(path) == 'quarantined'
    assert path.exists()  # Cannot discard private result without a receipt.
    record = json.loads(path.read_text(encoding='utf-8'))
    assert record['dead_letter'] is True
    assert record['dead_letter_status'] == 422
    assert record['rejection_notified'] is True
    assert len(events) == 2
    assert events[0][0].endswith('/result')
    assert events[1][0].endswith('/reject')
    assert events[1][1] == {'lease':'synthetic-lease',
                            'reason':'result_schema_rejected'}
    assert worker._deliver(path) == 'quarantined'
    assert len(events) == 2  # No duplicate notification or GPU retry.


def test_worker_retries_rejection_notice_after_transient_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('STT_WORKER_KEY', 'test-secret')
    from server import worker
    monkeypatch.setattr(worker, 'OUTBOX', tmp_path / 'outbox')
    endpoints = []

    class Client:
        def post(self, endpoint, **kwargs):
            endpoints.append(endpoint)
            if endpoint.endswith('/result'):
                return httpx.Response(422, json={},
                    request=httpx.Request('POST','https://localhost'+endpoint))
            if len(endpoints) == 2:
                raise httpx.ConnectError('offline',
                    request=httpx.Request('POST','https://localhost'+endpoint))
            return httpx.Response(200,json={'ok':True},
                request=httpx.Request('POST','https://localhost'+endpoint))

    monkeypatch.setattr(worker, 'client', Client())
    job = {'id':'e' * 32,'lease':'synthetic-lease','expires':time.time()+3600}
    path = worker._save_outbox(job, {'segments':[],'duration':0})
    assert worker._deliver(path) == 'pending'
    record = json.loads(path.read_text(encoding='utf-8'))
    assert record['dead_letter_status'] == 422
    assert not record.get('rejection_notified')
    assert worker._deliver(path,now=record['retry_at']-0.1) == 'pending'
    assert worker._deliver(path,now=record['retry_at']+0.1) == 'quarantined'
    assert json.loads(path.read_text(encoding='utf-8'))['rejection_notified']
    assert len(endpoints) == 3
