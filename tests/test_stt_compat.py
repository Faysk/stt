"""Compatibility regressions for migration issue #13 (no real secrets/media)."""
import importlib
import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server.config import getenv, required


def test_new_and_legacy_configuration_are_equivalent(monkeypatch):
    monkeypatch.delenv('TTS_ADMIN_KEY', raising=False)
    monkeypatch.delenv('STT_ADMIN_KEY', raising=False)
    assert getenv('TTS_ADMIN_KEY', 'fallback') == 'fallback'
    with pytest.raises(RuntimeError, match='Missing STT/TTS configuration: ADMIN_KEY'):
        required('TTS_ADMIN_KEY')

    monkeypatch.setenv('TTS_ADMIN_KEY', 'legacy-value')
    assert getenv('TTS_ADMIN_KEY') == 'legacy-value'
    monkeypatch.setenv('STT_ADMIN_KEY', 'legacy-value')
    assert required('TTS_ADMIN_KEY') == 'legacy-value'
    monkeypatch.setenv('STT_ADMIN_KEY', 'new-different-value')
    with pytest.raises(RuntimeError) as err:
        getenv('TTS_ADMIN_KEY')
    assert 'ADMIN_KEY' in str(err.value)
    assert 'legacy-value' not in str(err.value)
    assert 'new-different-value' not in str(err.value)
    monkeypatch.delenv('TTS_ADMIN_KEY')
    assert required('TTS_ADMIN_KEY') == 'new-different-value'


def test_new_prefix_upgrade_reuses_existing_sqlite_and_session(tmp_path, monkeypatch):
    for suffix in ('DATA', 'ADMIN_KEY', 'WORKER_KEY', 'EDGE_KEY', 'REQUIRE_EDGE'):
        monkeypatch.delenv('STT_' + suffix, raising=False)
        monkeypatch.delenv('TTS_' + suffix, raising=False)

    monkeypatch.setenv('TTS_DATA', str(tmp_path))
    monkeypatch.setenv('TTS_ADMIN_KEY', 'admin-test')
    monkeypatch.setenv('TTS_WORKER_KEY', 'worker-test')
    monkeypatch.setenv('TTS_REQUIRE_EDGE', '0')
    session = {'x-session-key': 'f' * 64}
    with patch('dotenv.load_dotenv', lambda *a, **kw: None):
        import server.app as module
        module = importlib.reload(module)
        with TestClient(module.app) as client:
            sent = client.post('/api/jobs/upload', headers=session,
                               files={'file': ('x.wav', io.BytesIO(b'fixture'), 'audio/wav')})
            assert sent.status_code == 201
            jid = sent.json()['id']
        assert (tmp_path / 'db' / 'tts.sqlite3').exists()
        monkeypatch.delenv('TTS_DATA')
        monkeypatch.delenv('TTS_ADMIN_KEY')
        monkeypatch.delenv('TTS_WORKER_KEY')
        monkeypatch.delenv('TTS_REQUIRE_EDGE')
        monkeypatch.setenv('STT_DATA', str(tmp_path))
        monkeypatch.setenv('STT_ADMIN_KEY', 'admin-test')
        monkeypatch.setenv('STT_WORKER_KEY', 'worker-test')
        monkeypatch.setenv('STT_REQUIRE_EDGE', '0')
        module = importlib.reload(module)
        with TestClient(module.app) as client:
            rows = client.get('/api/jobs', headers=session)
            assert rows.status_code == 200
            assert jid in [r['id'] for r in rows.json()]
            assert client.get('/api/admin', headers={'x-admin-key': 'admin-test'}).status_code == 200
        assert not (tmp_path / 'db' / 'stt.sqlite3').exists()
