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
    monkeypatch.setenv("STT_S7_ENABLED","0")
    monkeypatch.setenv("STT_S7_SIDE_WORKER","0")
    import server.app as module
    return importlib.reload(module)

ADMIN={"x-admin-key":"admin-test"}
S7={"x-worker-key":"s7-dedicated-test"}
MASTER={"x-worker-key":"master-worker-test"}
CAPS={"engine":"whisper.cpp","models":[{"id":"tiny","revision":"ggml-tiny"}],
      "compute_types":{"cpu":["float32"],"cuda":[]}}

def test_s7_http_transport_sends_compatible_user_agent_and_scoped_key(monkeypatch):
    """Preserve the exact UA verified on the production route without hiding worker auth."""
    from s7 import worker as w

    observed = {}

    class FakeResponse:
        status = 200

    class FakeOpener:
        def open(self, request, timeout):
            observed["user_agent"] = request.get_header("User-agent")
            observed["worker_key"] = request.get_header("X-worker-key")
            observed["content_type"] = request.get_header("Content-type")
            observed["url"] = request.full_url
            return FakeResponse()

    monkeypatch.setattr(w, "build_opener", lambda *handlers: FakeOpener())
    code, _ = w.call(
        {"url": "https://stt.faysk.dev", "key": "scoped-test-key"},
        "/api/worker/claim", "POST", b'{}'
    )
    assert code == 200
    assert observed == {
        "user_agent": "Mozilla/5.0",
        "worker_key": "scoped-test-key",
        "content_type": "application/json",
        "url": "https://stt.faysk.dev/api/worker/claim",
    }


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
        assert c.put("/api/admin/settings",headers=ADMIN,json=requested).status_code==409
        monkeypatch.setenv("STT_S7_ENABLED","1")
        assert c.put("/api/admin/settings",headers=ADMIN,json=requested).status_code==200
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
        desired=dict(worker="s7-edge",model="tiny",device="cpu",compute="float32",paused=False)
        assert c.put("/api/admin/settings",headers=ADMIN,json=desired).status_code==409
        # Register first: until an online compatible worker is seen, admin opt-in fails.
        assert c.post("/api/worker/claim",headers=S7,json={"id":"s7-edge","info":CAPS}).json()["job"] is None
        assert c.put("/api/admin/settings",headers=ADMIN,json=desired).status_code==200
        claimed=c.post("/api/worker/claim",headers=S7,json={"id":"s7-edge","info":CAPS})
        assert claimed.status_code==200
        job=claimed.json()["job"]
        assert job["id"]=="voice-wav"
        assert job["input_type"]=="wav"
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

def test_s7_can_run_as_secondary_alongside_primary(tmp_path,monkeypatch):
    module=api(tmp_path,monkeypatch)
    monkeypatch.setenv("STT_S7_ENABLED","1")
    monkeypatch.setenv("STT_S7_SIDE_WORKER","1")
    with TestClient(module.app) as c:
        now=__import__("time").time()
        with module.db() as d:
            for title,priority in (("speech.wav",9),("recording.mp4",8)):
                d.execute("""INSERT INTO jobs(id,owner,title,source,status,created,expires,
                           input_bytes,priority) VALUES(?,?,?,?,?,?,?,?,?)""",
                          (title.replace(".","-"),"owner",title,"upload","queued",
                           now,now+7200,2048,priority))
        # Settings remain pointing at notebook, model small, CPU int8.
        assert c.get("/api/admin",headers=ADMIN).json()["settings"]["worker"]=="notebook"
        assert c.post("/api/worker/claim",headers=S7,
                      json={"id":"s7-edge","info":CAPS}).json()["job"]["id"]=="speech-wav"
        primary=c.post("/api/worker/claim",headers=MASTER,json={"id":"notebook","info":{}})
        assert primary.status_code==200
        assert primary.json()["job"]["id"]=="recording-mp4"
        assert primary.json()["job"]["model"]=="small"
        admin=c.get("/api/admin",headers=ADMIN).json()
        assert admin["s7_side_worker"] is True
        assert {x["worker"] for x in admin["jobs"]}=={"s7-edge","notebook"}


def test_s7_storage_floor_and_workspace_budget(tmp_path,monkeypatch):
    from s7 import worker as w
    from types import SimpleNamespace
    cfg={'work':tmp_path}
    monkeypatch.setattr(w.shutil,'disk_usage',lambda _:SimpleNamespace(free=w.MIN_INTERNAL_FREE+w.JOB_RESERVE*2))
    w.ensure_storage(cfg,reserve=w.JOB_RESERVE)
    monkeypatch.setattr(w.shutil,'disk_usage',lambda _:SimpleNamespace(free=w.MIN_INTERNAL_FREE-1))
    with pytest.raises(w.WorkerError):w.ensure_storage(cfg)
    monkeypatch.setattr(w.shutil,'disk_usage',lambda _:SimpleNamespace(free=w.MIN_INTERNAL_FREE+w.JOB_RESERVE*2))
    monkeypatch.setattr(w,'WORKSPACE_BUDGET',128)
    (tmp_path/'own-cache').write_bytes(b'x'*120)
    with pytest.raises(w.WorkerError):w.ensure_storage(cfg,reserve=16)


def test_s7_stops_child_when_storage_floor_is_crossed(monkeypatch):
    import threading,time
    from s7 import worker as w
    from types import SimpleNamespace
    child=SimpleNamespace(poll=lambda:None)
    killed=[]
    monkeypatch.setattr(w.subprocess,'Popen',lambda *a,**kw:child)
    monkeypatch.setattr(w,'kill',lambda p:killed.append(p))
    def crossed():raise w.WorkerError('storage reserve')
    with pytest.raises(w.WorkerError):
        w.run_bounded(['fixture'],time.time()+10,'infer',threading.Event(),None,crossed)
    assert killed==[child]


def test_s7_outbox_rejects_oversized_result_before_writing(tmp_path):
    from s7 import worker as w
    cfg={'work':tmp_path}
    job={'id':'fixture','lease':'test-lease','expires':99999999999}
    with pytest.raises(w.WorkerError):w.spool(cfg,job,{'text':'x'*(1024**2)})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('channels',[1,2])
@pytest.mark.parametrize('input_type',['wav','mp3','ogg','m4a'])
def test_s7_validates_real_wave_reader_after_normalization(tmp_path,monkeypatch,channels,input_type):
    import threading,time,wave
    from s7 import worker as w
    output=tmp_path/'normalized.wav'
    def fake_ffmpeg(*args,**kwargs):
        command=args[0]
        assert command[command.index('-f')+1]==('mov' if input_type=='m4a' else input_type)
        assert command[command.index('-protocol_whitelist')+1]=='file,pipe'
        with wave.open(str(output),'wb') as audio:
            audio.setnchannels(channels);audio.setsampwidth(2);audio.setframerate(16000)
            audio.writeframes(bytes(16000*2*channels))
    monkeypatch.setattr(w,'run_bounded',fake_ffmpeg)
    if channels==1:
        assert w.decode_audio(tmp_path/'input.media',output,time.time()+10,threading.Event(),input_type)==1
    else:
        with pytest.raises(w.WorkerError):
            w.decode_audio(tmp_path/'input.media',output,time.time()+10,threading.Event(),input_type)


@pytest.mark.parametrize('extension',['ogg','m4a'])
def test_s7_ogg_eligibility_still_restricts_size_and_source(tmp_path,monkeypatch,extension):
    module=api(tmp_path,monkeypatch)
    item=dict(source='upload',input_bytes=384738,title='voice.'+extension)
    assert module.s7_eligible(item)
    assert not module.s7_eligible(dict(item,input_bytes=8*1024**2+1))
    assert not module.s7_eligible(dict(item,source='https://example.invalid'))
    assert not module.s7_eligible(dict(item,title='voice.m3u8'))


@pytest.mark.parametrize('broken_segment',[False,True])
def test_invalid_utf8_token_does_not_discard_valid_transcription(tmp_path,broken_segment):
    from s7 import worker as w
    path=tmp_path/'output.json'
    text=b'Ol\xc3\xa1 mundo' if not broken_segment else b'Ol\xc3 mundo'
    path.write_bytes(b'{"transcription":[{"offsets":{"from":0,"to":1000},"text":"'+text+b'","tokens":[{"text":"\xc3"}]}],"result":{"language":"pt"}}')
    result=w.parse_transcription(path,1)
    assert result['result_state']==('partial' if broken_segment else 'complete')
    assert result['segments'][0]['text']==('Ol\ufffd mundo' if broken_segment else 'Olá mundo')
