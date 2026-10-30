"""S7 worker integration safety: authentication, eligibility and whisper JSON contract."""
import importlib
import json
import os
import stat
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

def api(tmp_path, monkeypatch):
    monkeypatch.setenv("STT_DATA",str(tmp_path))
    monkeypatch.setenv("STT_ADMIN_KEY","admin-test")
    monkeypatch.setenv("STT_WORKER_KEY","master-worker-test")
    monkeypatch.setenv("STT_S7_WORKER_KEY","s7-dedicated-test")
    monkeypatch.setenv("STT_REQUIRE_EDGE","0")
    import server.app as module
    return importlib.reload(module)

ADMIN={"x-admin-key":"admin-test"}
S7={"x-worker-key":"s7-dedicated-test"}
MASTER={"x-worker-key":"master-worker-test"}
CAPS={"engine":"whisper.cpp","models":[{"id":"tiny","revision":"ggml-tiny"}],
      "compute_types":{"cpu":["float32"],"cuda":[]}}

def test_s7_scoped_key_and_opt_in(tmp_path,monkeypatch):
    module=api(tmp_path,monkeypatch)
    with TestClient(module.app) as c:
        assert c.post("/api/worker/claim",headers=S7,json={"id":"notebook"}).status_code==403
        assert c.post("/api/worker/model-preloads/claim",headers=S7,
                      json={"worker":"notebook"}).status_code==403
        claim=c.post("/api/worker/claim",headers=S7,json={"id":"s7-edge","info":CAPS})
        assert claim.status_code==200 and claim.json()["job"] is None
        assert c.post("/api/worker/model-preloads/claim",headers=S7,
                      json={"worker":"s7-edge"}).json()["operation"] is None
        requested=dict(worker="s7-edge",model="tiny",device="cpu",compute="float32",paused=False)
        assert c.put("/api/admin/settings",headers=ADMIN,json=requested).status_code==200
        monkeypatch.setenv("STT_S7_ENABLED","1")
        # S7 must not advertise a model that does not actually exist.
        assert c.post("/api/worker/claim",headers=S7,
                      json={"id":"s7-edge","info":{**CAPS,"models":[]}}).json()["job"] is None
        # Other worker shared token is still accepted; no implicit S7 takeover.
        assert c.post("/api/worker/claim",headers=MASTER,json={"id":"notebook"}).status_code==200

def test_s7_only_claims_small_audio_uploads(tmp_path,monkeypatch):
    module=api(tmp_path,monkeypatch)
    monkeypatch.setenv("STT_S7_ENABLED","1")
    with TestClient(module.app) as c:
        # Seed jobs through DB so no HTTP upload fixture hides the routing invariant.
        now=__import__("time").time()
        with module.db() as d:
            for title,source,size,priority in [
                ("video", "https://www.youtube.com/watch?v=AAAAAAAAAAA", 0, 7),
                ("long.wav","upload",9*1024*1024,6),
                ("footage.mp4","upload",1024,5),
                ("voice.wav","upload",1024,4)]:
                jid=title.replace(".","-")
                d.execute("""INSERT INTO jobs(id,owner,title,source,status,created,expires,
                           input_bytes,priority) VALUES(?,?,?,?,?,?,?,?,?)""",
                          (jid,"owner",title,source,"queued",now,now+7200,size,priority))
        assert c.post("/api/worker/claim",headers=S7,json={"id":"s7-edge","info":CAPS}).json()["job"] is None
        desired=dict(worker="s7-edge",model="tiny",device="cpu",compute="float32",paused=False)
        assert c.put("/api/admin/settings",headers=ADMIN,json=desired).status_code==409
        c.post("/api/worker/claim",headers=S7,json={"id":"s7-edge","info":CAPS})
        assert c.put("/api/admin/settings",headers=ADMIN,json=desired).status_code==200
        claimed=c.post("/api/worker/claim",headers=S7,json={"id":"s7-edge","info":CAPS})
        assert claimed.status_code==200
        job=claimed.json()["job"]
        assert job["id"]=="voice-wav"
        assert c.post("/api/worker/video/heartbeat",headers=S7,
                      json={"lease":job["lease"]}).status_code==409
        ok=c.post("/api/worker/voice-wav/heartbeat",headers=S7,
                  json={"lease":job["lease"],"stage":"transcribing"})
        assert ok.status_code==200
        # Scoped key cannot access another worker's leased jobs, even with a guessed lease.
        with module.db() as d:
            d.execute("UPDATE jobs SET worker='notebook' WHERE id=?",(job["id"],))
        assert c.post("/api/worker/voice-wav/heartbeat",headers=S7,
                      json={"lease":job["lease"]}).status_code==403
        assert c.get("/api/worker/voice-wav/input",headers={**S7,"x-lease":job["lease"]}).status_code==403
        assert c.post("/api/worker/voice-wav/result",headers=S7,
                      json={"lease":job["lease"],"duration":1}).status_code==403

def test_whisper_cpp_json_offsets_and_safety(tmp_path):
    from s7 import worker as w
    path=tmp_path/"output.json"
    path.write_text(json.dumps({
        "result":{"language":"pt"},
        "transcription":[{"offsets":{"from":1000,"to":3500},"text":"Olá mundo"}]
    }),encoding="utf-8")
    out=w.parse_transcription(path,4)
    assert out["language"]=="pt"
    assert out["segments"]==[{"start":1.0,"end":3.5,"text":"Olá mundo"}]
    assert out["result_state"]=="complete"
    with pytest.raises(w.WorkerError):
        w.parse_transcription(path,1)
    path.write_text(json.dumps({"result":{"language":"pt"},"transcription":[]}),encoding="utf-8")
    assert w.parse_transcription(path,4)["result_state"]=="silence"

@pytest.mark.skipif(os.name=="nt",reason="POSIX chmod/mode and Termux-only source")
def test_s7_key_file_rejects_world_readable(tmp_path,monkeypatch):
    from s7 import worker as w
    key=tmp_path/"secret"
    key.write_text("k"*64)
    root=tmp_path/"engine"
    root.mkdir()
    monkeypatch.setenv("STT_S7_KEY_FILE",str(key))
    monkeypatch.setenv("STT_S7_ROOT",str(root))
    monkeypatch.setenv("STT_S7_WORK",str(tmp_path/"work"))
    monkeypatch.setenv("STT_URL","https://stt.faysk.dev")
    key.chmod(0o644)
    with pytest.raises(w.WorkerError):
        w.config_from_environment()
    key.chmod(0o600)
    cfg=w.config_from_environment()
    assert cfg["key"]=="k"*64
    assert cfg["work"].stat().st_mode & 0o077==0
