"""Crash, transport and media-limit regressions for audit findings #20–#22.

No internet, user media, local GPU or production resources.
"""
import importlib
import io
import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient


SESSION = {'x-session-key': 'a' * 64}
WORKER = {'x-worker-key': 'test-worker'}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('TTS_DATA', str(tmp_path))
    monkeypatch.setenv('TTS_ADMIN_KEY', 'test-admin')
    monkeypatch.setenv('TTS_WORKER_KEY', 'test-worker')
    monkeypatch.setenv('TTS_REQUIRE_EDGE', '0')
    monkeypatch.setenv('TTS_OUTBOX', str(tmp_path / 'worker-outbox'))
    with patch('dotenv.load_dotenv', lambda *a, **kw: None):
        import server.app as mod
        mod = importlib.reload(mod)
        with TestClient(mod.app) as client:
            yield client, mod


def module_worker(monkeypatch, tmp_path):
    monkeypatch.setenv('TTS_WORKER_KEY', 'test-worker')
    monkeypatch.setenv('TTS_OUTBOX', str(tmp_path / 'outbox'))
    with patch('dotenv.load_dotenv', lambda *a, **kw: None):
        import server.worker as worker
        return importlib.reload(worker)


def submit(client):
    result = client.post('/api/jobs/upload', headers=SESSION,
                         files={'file': ('test.wav', io.BytesIO(b'fixture'), 'audio/wav')})
    assert result.status_code == 201, result.text
    return result.json()['id']


def claim(client):
    result = client.post('/api/worker/claim', headers=WORKER, json={'id': 'notebook'})
    assert result.status_code == 200, result.text
    return result.json()['job']


def response(status, body=None):
    return httpx.Response(status, json=body or {}, request=httpx.Request('POST', 'https://fixture.invalid/result'))


def test_permanent_http_422_preserves_payload_but_unblocks_claims(tmp_path, monkeypatch):
    w = module_worker(monkeypatch, tmp_path)
    job = {'id': 'permanent-422', 'lease': 'lease-1', 'expires': time.time() + 600}
    path = w._save_outbox(job, {'duration': 1, 'text': 'valuable transcript'})
    calls = []

    class Client:
        def post(self, *args, **kwargs):
            calls.append(1)
            return response(422, {'detail': 'Unprocessable Entity'})

    monkeypatch.setattr(w, 'client', Client())
    assert w._drain_outbox() is False
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['dead_letter'] is True
    assert data['dead_letter_status'] == 422
    assert data['payload']['text'] == 'valuable transcript'
    assert w._drain_outbox() is False
    assert len(calls) == 1


def test_transient_503_retries_and_ack_removes_spool(tmp_path, monkeypatch):
    w = module_worker(monkeypatch, tmp_path)
    job = {'id': 'transient-503', 'lease': 'lease-1', 'expires': time.time() + 600}
    path = w._save_outbox(job, {'text': 'keep me'})
    calls = []

    class Client:
        def post(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return response(503)
            return response(200, {'ok': True, 'receipt': 'a' * 64})

    monkeypatch.setattr(w, 'client', Client())
    now = time.time()
    assert w._deliver(path, now) == 'pending'
    assert w._deliver(path, now + 10) == 'acked'
    assert not path.exists()
    assert len(calls) == 2


def test_invalid_success_receipt_is_quarantined(tmp_path, monkeypatch):
    w = module_worker(monkeypatch, tmp_path)
    path = w._save_outbox({'id': 'bad-ack', 'lease': 'lease-2', 'expires': time.time() + 600},
                          {'text': 'do not lose'})

    class Client:
        def post(self, *args, **kwargs):
            return response(200, {'ok': True, 'receipt': 'bogus'})

    monkeypatch.setattr(w, 'client', Client())
    assert w._deliver(path) == 'quarantined'
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['dead_letter_reason'] == 'invalid_receipt'
    assert saved['payload']['text'] == 'do not lose'
    assert w._drain_outbox() is False


def test_corrupt_spool_does_not_block_new_claims(tmp_path, monkeypatch):
    w = module_worker(monkeypatch, tmp_path)
    w.OUTBOX.mkdir()
    (w.OUTBOX / 'bad.json').write_text('{this is not json', encoding='utf-8')
    assert w._drain_outbox() is False
    assert (w.OUTBOX / 'bad.json').exists()


def test_committed_artifact_generation_and_untimed_srt_isolation(api):
    client, mod = api
    jid = submit(client)
    lease = claim(client)['lease']
    # Simulate a stale flat export from the old layout: it must not be served.
    (mod.BLOB / jid / 'transcript.srt').write_text('STALE SUBTITLE', encoding='utf-8')
    payload = {'lease': lease, 'duration': 4, 'timing': 'none',
               'result_state': 'complete', 'text': 'Novo texto sem tempo'}
    sent = client.post(f'/api/worker/{jid}/result', headers=WORKER, json=payload)
    assert sent.status_code == 200, sent.text
    with mod.db() as db:
        row = db.execute('SELECT artifact_layout,receipt_hash FROM jobs WHERE id=?', (jid,)).fetchone()
    assert row['artifact_layout'] == 'generations-v1'
    active = mod.BLOB / jid / 'results' / row['receipt_hash']
    assert {p.name for p in active.iterdir()} == {'transcript.txt', 'transcript.md', 'transcript.json'}
    assert client.get(f'/api/jobs/{jid}/download/txt', headers=SESSION).text == 'Novo texto sem tempo'
    assert client.get(f'/api/jobs/{jid}/download/srt', headers=SESSION).status_code == 404
    assert client.get(f'/api/jobs/{jid}', headers=SESSION).json()['text'] == 'Novo texto sem tempo'
    replay = client.post(f'/api/worker/{jid}/result', headers=WORKER, json=payload)
    assert replay.status_code == 200 and replay.json()['duplicate'] is True


def test_sql_rollback_never_exposes_orphan_generation_and_retry_recovers(api, monkeypatch):
    client, mod = api
    jid = submit(client)
    lease = claim(client)['lease']
    payload = {'lease': lease, 'duration': 3, 'result_state': 'complete',
               'segments': [{'start': 0, 'end': 3, 'text': 'survives rollback'}]}
    real_event = mod.obs.event
    raised = []

    def once(db, id, kind, *args, **kwargs):
        if kind == 'done' and not raised:
            raised.append(True)
            raise OSError('synthetic transaction failure after disk publication')
        return real_event(db, id, kind, *args, **kwargs)

    monkeypatch.setattr(mod.obs, 'event', once)
    with pytest.raises(OSError):
        client.post(f'/api/worker/{jid}/result', headers=WORKER, json=payload)
    with mod.db() as db:
        row = db.execute('SELECT status,receipt_hash,artifact_layout FROM jobs WHERE id=?', (jid,)).fetchone()
    assert row['status'] == 'running'
    assert row['receipt_hash'] is None and row['artifact_layout'] is None
    assert client.get(f'/api/jobs/{jid}/download/srt', headers=SESSION).status_code == 404
    orphan = list((mod.BLOB / jid / 'results').iterdir())
    assert len(orphan) == 1 and orphan[0].is_dir()
    retry = client.post(f'/api/worker/{jid}/result', headers=WORKER, json=payload)
    assert retry.status_code == 200, retry.text
    assert client.get(f'/api/jobs/{jid}/download/txt', headers=SESSION).text == 'survives rollback'
    assert len(list((mod.BLOB / jid / 'results').iterdir())) == 1


def test_previous_flat_layout_remains_readable_after_schema_migration(api):
    client, mod = api
    jid = submit(client)
    folder = mod.BLOB / jid
    (folder / 'transcript.txt').write_text('legacy transcription', encoding='utf-8')
    with mod.db() as db:
        db.execute("UPDATE jobs SET status='done',artifact_layout=NULL,receipt_hash=? WHERE id=?",
                   ('f' * 64, jid))
    assert client.get(f'/api/jobs/{jid}', headers=SESSION).json()['text'] == 'legacy transcription'
    assert client.get(f'/api/jobs/{jid}/download/txt', headers=SESSION).text == 'legacy transcription'


def test_qwen_incremental_audio_and_network_budgets(monkeypatch):
    import numpy as np
    from server import qwen_engine as qwen

    monkeypatch.setattr(qwen, 'MAX_AUDIO_SAMPLES', 6)
    monkeypatch.setattr(qwen, 'SAMPLE_RATE', 2)
    monkeypatch.setattr(qwen, 'MAX_MEDIA_BYTES', 10)

    class Frame:
        def to_ndarray(self):
            return np.ones((1, 4), dtype=np.float32)

    class Source:
        streams = [type('Audio', (), {'type': 'audio'})()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def decode(self, stream):
            yield Frame()
            yield Frame()

    class AudioResampler:
        def __init__(self, **kwargs):
            pass

        def resample(self, frame):
            return [frame] if frame is not None else []

    class AV:
        @staticmethod
        def open(*args):
            return Source()

    AV.AudioResampler = AudioResampler

    windows = qwen.iter_windows('synthetic.wav', AV, np, seconds=2)
    start, first = next(windows)
    assert start == 0 and len(first) == 4
    with pytest.raises(qwen.EngineError, match='QWEN_DURATION_EXCEEDED'):
        next(windows)
    qwen.enforce_download_progress({'downloaded_bytes': 10})
    with pytest.raises(qwen.EngineError, match='QWEN_DOWNLOAD_TOO_LARGE'):
        qwen.enforce_download_progress({'downloaded_bytes': 11})


def test_operational_privacy_retention_and_five_year_cleanup(api):
    client, mod = api
    now = time.time()
    stale = submit(client)
    ancient = submit(client)
    thirty_one_days = now - 31 * mod.TTL
    older_than_five_years = now - 5 * 365 * mod.TTL - mod.TTL
    with mod.db() as db:
        db.execute("""UPDATE jobs SET created=?,expires=?,client_tag='session-pseudonym',
                   network_tag='network-pseudonym',ui_language='pt' WHERE id=?""",
                   (thirty_one_days - 3600, thirty_one_days, stale))
        db.execute("""UPDATE jobs SET created=?,expires=?,status='expired',
                   purged_at=? WHERE id=?""",
                   (older_than_five_years - 3600, older_than_five_years,
                    older_than_five_years, ancient))
        db.execute('UPDATE events SET at=? WHERE job=?', (thirty_one_days, stale))
        # Daily aggregates are intentionally retained for 5 years, not forever.
        mod.obs.record(db, 'test-history', 10, 10, None, 10, 0,
                       now=older_than_five_years)
    # The ancient row is marked physically purged in the fixture.
    import shutil
    shutil.rmtree(mod.BLOB / ancient)
    mod.maintenance()
    with mod.db() as db:
        row = db.execute('SELECT status,owner,client_tag,network_tag,ui_language FROM jobs WHERE id=?',
                         (stale,)).fetchone()
        assert row is not None and row['status'] == 'expired'
        assert row['owner'] == ''
        assert row['client_tag'] is None and row['network_tag'] is None
        assert row['ui_language'] is None
        assert db.execute('SELECT count(*) FROM events WHERE job=?', (stale,)).fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM jobs WHERE id=?', (ancient,)).fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM telemetry WHERE bucket<?',
                          (now - mod.AGGREGATE_RETENTION,)).fetchone()[0] == 0


def test_corrupt_outbox_eventually_expires(tmp_path, monkeypatch):
    import os
    w = module_worker(monkeypatch, tmp_path)
    w.OUTBOX.mkdir()
    corrupt = w.OUTBOX / 'expired-corrupt.json'
    corrupt.write_text('broken', encoding='utf-8')
    old = time.time() - 2 * 86400
    os.utime(corrupt, (old, old))
    assert w._drain_outbox() is False
    assert not corrupt.exists()


def test_whisper_decoded_duration_rejects_untrusted_long_audio(monkeypatch):
    from server.infer import bounded_audio_duration

    class Codec:
        sample_rate = 2

    class Stream:
        type = 'audio'
        codec_context = Codec()

    class Frame:
        def __init__(self, count):
            self.sample_rate = 2
            self.samples = count

    class Container:
        streams = [Stream()]

        def decode(self, stream):
            assert stream is self.streams[0]
            yield Frame(3)
            yield Frame(3)

    with pytest.raises(ValueError, match='Duração máxima'):
        bounded_audio_duration(Container(), maximum=2)
    assert bounded_audio_duration(Container(), maximum=4) == 3
