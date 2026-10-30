"""Executor-scoped model preload API regression tests (no model downloads)."""
import importlib

import pytest
from fastapi.testclient import TestClient

ADMIN = {"x-admin-key": "admin-key"}
WORKER = {"x-worker-key": "worker-key"}
SESSION = {"x-session-key": "a" * 64}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("STT_DATA", str(tmp_path))
    monkeypatch.setenv("STT_ADMIN_KEY", "admin-key")
    monkeypatch.setenv("STT_WORKER_KEY", "worker-key")
    monkeypatch.setenv("STT_REQUIRE_EDGE", "0")
    import server.app as module
    module = importlib.reload(module)
    with TestClient(module.app) as client:
        yield client, module


def test_preload_auth_idempotency_and_worker_report(environment):
    client, _ = environment
    request = {"worker": "notebook", "model": "small"}

    assert client.post("/api/admin/model-preloads", json=request).status_code == 401
    assert client.post("/api/admin/model-preloads", headers=ADMIN, json=request).status_code == 404
    assert client.post("/api/worker/claim", headers=WORKER,
                       json={"id": "notebook", "info": {"cpu": 28}}).status_code == 200
    assert client.post("/api/admin/model-preloads", headers=ADMIN,
                       json={**request, "model": "../../other"}).status_code == 400

    initial = client.post("/api/admin/model-preloads", headers=ADMIN, json=request)
    assert initial.status_code == 202
    operation = initial.json()
    second = client.post("/api/admin/model-preloads", headers=ADMIN, json=request)
    assert second.json()["id"] == operation["id"]
    assert client.post("/api/worker/model-preloads/claim",
                       json={"worker": "notebook"}).status_code == 401

    assigned = client.post("/api/worker/model-preloads/claim", headers=WORKER,
                           json={"worker": "notebook"})
    assert assigned.json()["operation"]["id"] == operation["id"]
    assert client.post("/api/worker/model-preloads/" + operation["id"] + "/report",
                       headers=WORKER, json={"worker": "other", "status": "ready"}).status_code == 404
    report = client.post("/api/worker/model-preloads/" + operation["id"] + "/report",
                         headers=WORKER, json={"worker": "notebook", "status": "ready",
                                               "revision": "abc123"})
    assert report.status_code == 200
    assert report.json()["status"] == "ready"
    overview = client.get("/api/admin", headers=ADMIN).json()
    assert any(p["id"] == operation["id"] and p["revision"] == "abc123"
               for p in overview["preloads"])


def test_preload_does_not_claim_while_transcription_is_pending(environment):
    client, _ = environment
    client.post("/api/worker/claim", headers=WORKER, json={"id": "notebook"})
    initial = client.post("/api/admin/model-preloads", headers=ADMIN,
                          json={"worker": "notebook", "model": "tiny"})
    assert initial.status_code == 202
    upload = client.post("/api/jobs/upload", headers=SESSION,
                         files={"file": ("clip.wav", b"test audio", "audio/wav")})
    assert upload.status_code == 201
    response = client.post("/api/worker/model-preloads/claim", headers=WORKER,
                           json={"worker": "notebook"})
    assert response.status_code == 200
    assert response.json()["operation"] is None


def test_advanced_compute_requires_reported_worker_capability(environment):
    client, _ = environment
    client.post("/api/worker/claim", headers=WORKER,
                json={"id": "notebook", "info": {"compute_types": {"cpu": ["int8_float32"]}}})
    request = {"worker": "notebook", "model": "small", "device": "cpu",
               "compute": "int8_float32", "paused": False}
    assert client.put("/api/admin/settings", headers=ADMIN, json=request).status_code == 200
    request["compute"] = "int8_bfloat16"
    assert client.put("/api/admin/settings", headers=ADMIN, json=request).status_code == 400
    request.update(worker="unknown", compute="int8_float32")
    assert client.put("/api/admin/settings", headers=ADMIN, json=request).status_code == 400


def test_gpu_name_is_snapshotted_from_worker_at_claim(environment):
    client, _ = environment
    config = {"worker": "notebook", "model": "small", "device": "cuda",
              "compute": "float16", "paused": False}
    assert client.put("/api/admin/settings", headers=ADMIN, json=config).status_code == 200
    upload = client.post("/api/jobs/upload", headers=SESSION,
                         files={"file": ("clip.wav", b"test audio", "audio/wav")})
    assert upload.status_code == 201
    job_id = upload.json()["id"]
    claimed = client.post("/api/worker/claim", headers=WORKER,
                          json={"id": "notebook",
                                "info": {"gpu": {"name": "Test NVIDIA GPU", "vram_mb": 8192}}})
    assert claimed.status_code == 200
    assert claimed.json()["job"]["id"] == job_id
    details = client.get("/api/admin/jobs/" + job_id, headers=ADMIN).json()["job"]
    assert details["gpu_name"] == "Test NVIDIA GPU"
    assert details["device"] == "cuda"
    assert details["compute"] == "float16"


def test_model_catalog_accepts_verified_pinned_snapshot_without_main_ref(tmp_path):
    from server.catalog import installed_models
    revision = "a" * 40
    base = tmp_path / "models--Systran--faster-whisper-tiny" / "snapshots" / revision
    base.mkdir(parents=True)
    (base / "model.bin").write_bytes(b"test binary")
    assert next(m for m in installed_models(tmp_path) if m["id"] == "tiny")["installed"] is False
    (base / "config.json").write_text("{}", encoding="utf-8")
    loaded = next(m for m in installed_models(tmp_path) if m["id"] == "tiny")
    assert loaded["installed"] is True
    assert loaded["revision"] == revision


def test_cpu_int16_is_only_accepted_with_worker_capability(environment):
    client, _ = environment
    config = {"worker": "notebook", "model": "small", "device": "cpu",
              "compute": "int16", "paused": False}
    client.post("/api/worker/claim", headers=WORKER,
                json={"id": "notebook", "info": {"compute_types": {"cpu": ["int8", "float32"]}}})
    assert client.put("/api/admin/settings", headers=ADMIN, json=config).status_code == 400
    client.post("/api/worker/claim", headers=WORKER,
                json={"id": "notebook", "info": {"compute_types": {"cpu": ["int8", "int16", "float32"]}}})
    assert client.put("/api/admin/settings", headers=ADMIN, json=config).status_code == 200
