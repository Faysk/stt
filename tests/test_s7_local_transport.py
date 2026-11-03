"""Offline security/transport regressions for S7 local API routing (#98)."""
import json
import stat
import pytest
from s7 import worker as w


def local_config(tmp_path, monkeypatch):
    monkeypatch.setenv("STT_URL", "http://127.0.0.1:8786")
    monkeypatch.setenv("STT_S7_ROOT", str(tmp_path / "models"))
    monkeypatch.setenv("STT_S7_WORK", str(tmp_path / "work"))
    key = tmp_path / "scoped.key"
    key.write_text("s" * 64, encoding="utf-8")
    key.chmod(0o600)
    monkeypatch.setenv("STT_S7_KEY_FILE", str(key))
    settings = tmp_path / "api.env"
    settings.write_text('STT_EDGE_KEY=' + json.dumps("e" * 64) + "\n", encoding="utf-8")
    settings.chmod(0o600)
    monkeypatch.setenv("STT_S7_LOCAL_API_ENV", str(settings))
    return key, settings


def test_local_worker_passes_both_keys_only_to_loopback(tmp_path, monkeypatch):
    local_config(tmp_path, monkeypatch)
    # On Windows, POSIX scoped-key file modes are not representable; exercise
    # the HTTP contract with a fixture config. Linux CI covers config_from_environment.
    cfg = (w.config_from_environment() if __import__('os').name != 'nt'
           else {'url':w.LOCAL_API,'key':'s'*64,'edge_key':'e'*64})
    headers = {}
    class Response:
        status = 200
    class Opener:
        def open(self, request, timeout):
            headers.update(request.headers)
            assert request.full_url == "http://127.0.0.1:8786/api/worker/claim"
            return Response()
    monkeypatch.setattr(w, "build_opener", lambda *args: Opener())
    assert w.call(cfg, "/api/worker/claim", "POST", b"{}")[0] == 200
    assert headers["X-edge-key"] == "e" * 64
    assert headers["X-worker-key"] == "s" * 64


def test_local_worker_rejects_untrusted_settings(tmp_path, monkeypatch):
    _, settings = local_config(tmp_path, monkeypatch)
    if __import__('os').name != 'nt':
        settings.chmod(0o644)
        with pytest.raises(w.WorkerError, match="permissions"):
            w.config_from_environment()
        settings.chmod(0o600)
    settings.write_text("STT_EDGE_KEY=bad\n", encoding="utf-8")
    with pytest.raises(w.WorkerError, match="authentication"):
        w.config_from_environment()
    settings.write_text('STT_EDGE_KEY=' + json.dumps("e" * 64) + "\n", encoding="utf-8")
    monkeypatch.setenv("STT_URL", "http://localhost:8786")
    with pytest.raises(w.WorkerError):
        w.config_from_environment()


def test_local_edge_secret_never_sent_to_remote(monkeypatch):
    class Response:
        status = 200
    class Opener:
        def open(self, request, timeout):
            assert request.get_header("X-edge-key") is None
            return Response()
    monkeypatch.setattr(w, "build_opener", lambda *args: Opener())
    assert w.call({"url": "https://stt.faysk.dev", "key": "worker"}, "/api/worker/claim", "POST", b"{}")[0] == 200
    with pytest.raises(w.WorkerError, match="nonlocal"):
        w.call({"url": "https://stt.faysk.dev", "key": "worker", "edge_key": "secret"},
               "/api/worker/claim", "POST", b"{}")
