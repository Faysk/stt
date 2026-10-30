"""S7 inventory is SSH-only and cannot silently become a public worker."""
import importlib

from fastapi.testclient import TestClient


def test_ssh_inventory_rejects_shell_metacharacters(monkeypatch):
    from server.ssh_targets import ssh_targets
    monkeypatch.setenv("STT_S7_SSH_HOST", "192.0.2.10;echo bad")
    monkeypatch.setenv("STT_S7_SSH_USER", "u0_a999")
    assert ssh_targets()[0]["configured"] is False
    assert ssh_targets()[0]["command"] is None
    monkeypatch.setenv("STT_S7_SSH_HOST", "192.0.2.10")
    monkeypatch.setenv("STT_S7_SSH_PORT", "8022")
    assert ssh_targets()[0]["command"] == "ssh -p 8022 u0_a999@192.0.2.10"
    monkeypatch.setenv("STT_S7_SSH_USER", "u0_a999\n--evil")
    assert ssh_targets()[0]["command"] is None
    monkeypatch.setenv("STT_S7_SSH_USER", "u0_a999")
    monkeypatch.setenv("STT_S7_SSH_PORT", "70000")
    assert ssh_targets()[0]["configured"] is False


def test_s7_ssh_is_admin_only_and_not_a_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("STT_DATA", str(tmp_path))
    monkeypatch.setenv("STT_ADMIN_KEY", "admin-test")
    monkeypatch.setenv("STT_WORKER_KEY", "worker-test")
    monkeypatch.setenv("STT_REQUIRE_EDGE", "0")
    monkeypatch.setenv("STT_S7_SSH_HOST", "192.0.2.10")
    monkeypatch.setenv("STT_S7_SSH_USER", "u0_a999")
    monkeypatch.setenv("STT_S7_SSH_PORT", "8022")
    import server.app as module
    module = importlib.reload(module)
    with TestClient(module.app) as client:
        assert client.get("/api/admin").status_code == 401
        data = client.get("/api/admin",headers={"x-admin-key":"admin-test"}).json()
        assert data["ssh_targets"][0]["command"] == "ssh -p 8022 u0_a999@192.0.2.10"
        assert all(w["id"] != "s7-edge" for w in data["workers"])
        request = {"worker":"s7-edge","model":"tiny","device":"cpu","compute":"int8","paused":False}
        response = client.put("/api/admin/settings",headers={"x-admin-key":"admin-test"},json=request)
        assert response.status_code == 409
        assert client.get("/api/admin",headers={"x-admin-key":"admin-test"}).json()["settings"]["worker"] != "s7-edge"
        client.post("/api/worker/claim",headers={"x-worker-key":"worker-test"},
                    json={"id":"s7-edge","info":{"compute_types":{"cpu":["int8"]},"models":[]}})
        assert client.put("/api/admin/settings",headers={"x-admin-key":"admin-test"},json=request).status_code == 409
        client.post("/api/worker/claim",headers={"x-worker-key":"worker-test"},
                    json={"id":"s7-edge","info":{"compute_types":{"cpu":["int8"]},"models":[{"id":"tiny"}]}})
        assert client.put("/api/admin/settings",headers={"x-admin-key":"admin-test"},json=request).status_code == 200
        request["device"] = "cuda"
        request["compute"] = "float16"
        assert client.put("/api/admin/settings",headers={"x-admin-key":"admin-test"},json=request).status_code == 409
