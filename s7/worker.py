"""Standalone S7 Edge STT worker (Android 8 / Termux, Python stdlib only).

Outbound HTTPS; deliberately no package imports from server/ or third-party wheels.
Single job at a time. Never touches the S7 Observatory or its SQLite databases.
"""
import hashlib
import json
import os
from pathlib import Path
import re
try:
    import resource
except ImportError:  # Windows CI tests parse and validate the adapter only.
    resource = None
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request

ID = "s7-edge"
# Cloudflare currently rejects urllib's default Python-urllib User-Agent (403),
# but accepts this exact value with the same scoped worker key (200).
# User-Agent is not authorization; x-worker-key and edge protection still apply.
CLIENT_USER_AGENT = "Mozilla/5.0"
MAX_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 120
SLEEP_SECONDS = 5
JOB_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")

class WorkerError(Exception):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def config_from_environment():
    root = Path(os.environ.get("STT_S7_ROOT", str(Path.home() / "stt-lab" / "whisper.cpp"))).expanduser().resolve()
    work = Path(os.environ.get("STT_S7_WORK", str(Path.home() / "stt-lab" / "stt-worker"))).expanduser().resolve()
    url = os.environ.get("STT_URL", "https://stt.faysk.dev").rstrip("/")
    parts = urlsplit(url)
    if (parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in ("localhost","127.0.0.1"))) or (
        parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("","/") or not parts.hostname
    ):
        raise WorkerError("STT_URL must be HTTPS or loopback HTTP origin")
    key_path = Path(os.environ.get("STT_S7_KEY_FILE", str(Path.home() / ".stt-s7-worker-key"))).expanduser()
    if key_path.is_symlink() or not key_path.is_file() or key_path.stat().st_mode & 0o077:
        raise WorkerError("S7 key file missing or permissions too broad; require chmod 600")
    key = key_path.read_text(encoding="utf-8").strip()
    if len(key) < 32:
        raise WorkerError("S7 key is too short")
    if work.is_symlink():
        raise WorkerError("Worker workspace cannot be a symlink")
    work.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not work.is_dir() or work.stat().st_mode & 0o077:
        raise WorkerError("Worker workspace must be private (chmod 700)")
    if work == root or root in work.parents or work in root.parents:
        raise WorkerError("Worker workspace must not contain the model repository")
    return dict(root=root,work=work,url=url,key=key)

def capabilities(cfg):
    cli = cfg["root"] / "build" / "bin" / "whisper-cli"
    model = cfg["root"] / "models" / "ggml-tiny.bin"
    # Never advertise a model merely because the CLI package is installed.
    ready = (cli.is_file() and os.access(cli,os.X_OK) and model.is_file()
             and model.stat().st_size >= 70*1024*1024 and shutil.which("ffmpeg") is not None)
    revision = "ggml-tiny"  # Model weight format; avoid pretending to have an HF CTranslate2 SHA.
    return dict(system="Android",machine=os.uname().machine,engine="whisper.cpp",
                ram_total_mb=read_memtotal(),cpu=os.cpu_count(),
                compute_types=dict(cpu=["float32"] if ready else [],cuda=[]),
                models=[dict(id="tiny",revision=revision)] if ready else [],
                qwen_models=[],native_backend="ggml-cpu",limits=dict(max_audio_seconds=MAX_SECONDS,
                max_input_bytes=MAX_BYTES),versions=dict(whisper_cpp="d1be6fd",python=sys.version.split()[0]))

def read_memtotal():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1])//1024
    except (OSError,ValueError,IndexError):
        pass
    return None

def post(cfg,path,payload,timeout=30):
    body=json.dumps(payload,ensure_ascii=False).encode("utf-8")
    return call(cfg,path,"POST",body,timeout)

def call(cfg,path,method,body=None,timeout=30,extra_headers=None):
    if not path.startswith("/api/worker/") or ".." in path or "?" in path:
        raise WorkerError("Invalid API route")
    headers={"x-worker-key":cfg["key"],"accept":"application/json",
             "user-agent":CLIENT_USER_AGENT}
    if body is not None: headers["content-type"]="application/json"
    if extra_headers: headers.update(extra_headers)
    request=Request(cfg["url"]+path,data=body,headers=headers,method=method)
    opener=build_opener(ProxyHandler({}),NoRedirect())
    try:
        response=opener.open(request,timeout=timeout)
        return response.status,response
    except HTTPError as error:
        # No remote message or credentials in logs.
        return error.code,error

def json_post(cfg,path,payload,timeout=30):
    code,response=post(cfg,path,payload,timeout)
    try:
        if code!=200 and code!=201:
            return code,None
        raw=response.read(1_048_577)
        if len(raw)>1_048_576: raise WorkerError("Oversized API response")
        return code,json.loads(raw)
    finally:
        response.close()

def atomic_json(path,data):
    temporary=path.with_name(path.name+"."+os.urandom(5).hex()+".tmp")
    flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd=os.open(temporary,flags,0o600)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(data,stream,ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory=os.open(path.parent,os.O_RDONLY | getattr(os,"O_DIRECTORY",0))
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)

def spool(cfg,job,result):
    name=job["id"]
    if not JOB_ID.fullmatch(name):
        raise WorkerError("Untrusted job ID")
    digest=hashlib.sha256(job["lease"].encode()).hexdigest()[:20]
    path=cfg["work"] / ("result-" + name + "-" + digest + ".json")
    atomic_json(path,{"id":name,"lease":job["lease"],"expires":job["expires"],
                      "payload":dict(result,lease=job["lease"]),"next_try":0,"attempts":0})
    return path

def deliver(cfg,path):
    data=json.loads(path.read_text(encoding="utf-8"))
    now=time.time()
    if now >= data["expires"]:
        path.unlink(missing_ok=True)
        return "expired"
    if now < data["next_try"]: return "pending"
    try:
        code,reply=json_post(cfg,"/api/worker/"+data["id"]+"/result",data["payload"],timeout=40)
    except (OSError,URLError,ValueError,TimeoutError,WorkerError):
        code,reply=503,None
    if code==200 and isinstance(reply,dict) and reply.get("ok") is True and re.fullmatch("[0-9a-f]{64}",str(reply.get("receipt",""))):
        path.unlink(missing_ok=True)
        return "acked"
    if code==409:
        path.unlink(missing_ok=True)  # Lease expired or completed by another executor.
        return "stale"
    if code in (401,403):
        return "auth_blocked"
    if code not in (408,425,429,502,503,504) and code < 500:
        data["next_try"]=data["expires"]  # Quarantine until TTL, never hot-loop on 422.
        data["quarantined_status"]=code
        atomic_json(path,data)
        return "quarantined"
    data["attempts"]+=1
    data["next_try"]=now+min(60,2**min(data["attempts"],6))
    atomic_json(path,data)
    return "pending"

def drain(cfg):
    # A hard power loss can leave a partially downloaded media directory.
    # Only remove stale worker-owned temp directories; never touch Observatory.
    for folder in cfg["work"].glob("job-*"):
        try:
            if (folder.is_dir() and not folder.is_symlink()
                    and time.time()-folder.stat().st_mtime>86400):
                shutil.rmtree(folder)
        except OSError:
            pass
    blocking=False
    for p in sorted(cfg["work"].glob("result-*.json")):
        try:
            status=deliver(cfg,p)
            if status!="acked" and status!="expired" and status!="stale": blocking=True
            if status=="auth_blocked": return "auth_blocked"
        except (OSError,ValueError,KeyError,TypeError):
            blocking=True
    return "pending" if blocking else "clear"

def kill(proc):
    if proc and proc.poll() is None:
        try: os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError: return
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait(timeout=3)

def run_bounded(argv,deadline,stage,alive,heartbeat):
    proc=None
    try:
        proc=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,start_new_session=True)
        while proc.poll() is None:
            if alive.is_set() or time.time()>=deadline:
                raise WorkerError("Lease lost or time budget exceeded")
            time.sleep(0.5)
        if proc.returncode:
            raise WorkerError(stage + " failed")
    finally:
        kill(proc)

def decode_audio(path,output,deadline,alive,input_type):
    # Force a strict demuxer from the server-signed job to prevent a disguised
    # playlist/manifest asking FFmpeg to fetch network or private local files.
    if input_type not in ("wav","mp3"):
        raise WorkerError("Unsupported media type on S7")
    command=["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-y",
             "-protocol_whitelist","file,pipe","-f",input_type,"-i",str(path),"-vn","-t",str(MAX_SECONDS+2),
             "-ac","1","-ar","16000","-c:a","pcm_s16le",str(output)]
    run_bounded(command,deadline,"decode",alive,None)
    import wave
    with wave.open(str(output),"rb") as audio:
        if (audio.getchannels()!=1 or audio.getsampwidth()!=2 or audio.getframerate()!=16000):
            raise WorkerError("Invalid normalized PCM")
        duration=audio.getnframes()/16000
    if duration<=0 or duration>MAX_SECONDS:
        raise WorkerError("Audio exceeds the S7 duration policy")
    return duration

def parse_transcription(path,duration):
    document=json.loads(path.read_text(encoding="utf-8"))
    segments=[]
    for chunk in document.get("transcription",[]):
        offsets=chunk["offsets"]
        start=max(0,float(offsets["from"])/1000)
        end=max(start,float(offsets["to"])/1000)
        if end>duration+0.5: raise WorkerError("Segment exceeds WAV duration")
        text=chunk["text"]
        if not isinstance(text,str) or len(text)>10000: raise WorkerError("Invalid transcription text")
        segments.append(dict(start=start,end=end,text=text))
    if not isinstance(document.get("transcription"),list) or len(segments)>30000:
        raise WorkerError("Invalid transcription JSON")
    lang=document.get("result",{}).get("language","")
    if not isinstance(lang,str) or len(lang)>20: lang=""
    return dict(segments=segments,language=lang,duration=duration,
                model_revision="ggml-tiny",result_state="complete" if segments else "silence")

def run_job(cfg,job):
    if (not JOB_ID.fullmatch(str(job.get("id",""))) or job.get("model")!="tiny"
            or job.get("engine")!="whisper" or job.get("device")!="cpu"
            or job.get("compute")!="float32" or job.get("source")!="upload"
            or job.get("input_type") not in ("wav","mp3")):
        raise WorkerError("Unexpected job configuration")
    deadline=min(float(job["expires"])-10,time.time()+600)
    if deadline <= time.time()+10: raise WorkerError("Job lease too short")
    stopped=threading.Event()
    lost=threading.Event()
    stage={"value":"preparing"}
    def heart():
        while not stopped.wait(15):
            try:
                code,_=json_post(cfg,"/api/worker/"+job["id"]+"/heartbeat",
                                  {"lease":job["lease"],"stage":stage["value"],"progress":0},timeout=12)
                if code in (401,403,409): lost.set()
            except (OSError,URLError,ValueError,TimeoutError,WorkerError):
                pass
    thread=threading.Thread(target=heart,daemon=True)
    thread.start()
    started=time.monotonic()
    before=resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    result=None
    try:
        with tempfile.TemporaryDirectory(prefix="job-",dir=cfg["work"]) as name:
            folder=Path(name)
            media=folder/"input.media"
            code,response=call(cfg,"/api/worker/"+job["id"]+"/input","GET",timeout=30,
                               extra_headers={"x-lease":job["lease"]})
            try:
                if code!=200: raise WorkerError("Input unavailable")
                with media.open("xb") as destination:
                    size=0
                    while True:
                        part=response.read(65536)
                        if not part: break
                        size+=len(part)
                        if size>MAX_BYTES or lost.is_set() or time.time()>=deadline:
                            raise WorkerError("Input exceeds budget or lease")
                        destination.write(part)
            finally: response.close()
            stage["value"]="loading"
            wav=folder/"audio.wav"
            duration=decode_audio(media,wav,deadline,lost,job["input_type"])
            stage["value"]="transcribing"
            cli=cfg["root"]/"build"/"bin"/"whisper-cli"
            model=cfg["root"]/"models"/"ggml-tiny.bin"
            prefix=folder/"whisper-output"
            cmd=[str(cli),"-ng","-m",str(model),"-f",str(wav),"-t","2","-l","auto",
                 "-oj","-of",str(prefix),"-np"]
            run_bounded(cmd,deadline,"inference",lost,None)
            stage["value"]="finishing"
            result=parse_transcription(Path(str(prefix)+".json"),duration)
            result["input_bytes"]=size
    except (WorkerError,OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        if lost.is_set() or time.time()>=job["expires"]:
            return None
        print("Job failure: "+type(exc).__name__,flush=True)
        result={"error":"O executor S7 não conseguiu processar este áudio; utilize outro executor."}
    finally:
        stopped.set()
        thread.join(timeout=2)
    if lost.is_set(): return None
    after=resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    result.update(elapsed=max(0,time.monotonic()-started),
                  cpu_seconds=max(0,(after.ru_utime+after.ru_stime)-(before.ru_utime+before.ru_stime)) if before and after else 0,
                  peak_rss_mb=max(0,after.ru_maxrss/1024) if after else 0,peak_vram_mb=0,
                  runtime={"engine":"whisper.cpp","backend":"ggml-cpu","device":"s7-edge"},
                  timing="segments")
    return spool(cfg,job,result)

def main():
    cfg=config_from_environment()
    print("S7 worker ready for outbound HTTPS (no public job until admin selects it)",flush=True)
    while True:
        status=drain(cfg)
        if status=="auth_blocked":
            raise WorkerError("Worker authentication denied; fix S7 key before restart")
        if status=="pending":
            time.sleep(SLEEP_SECONDS)
            continue
        caps=capabilities(cfg)
        try:
            code,reply=json_post(cfg,"/api/worker/claim",{"id":ID,"info":caps},timeout=20)
            if code in (401,403): raise WorkerError("Worker authentication denied")
            if code!=200 or not isinstance(reply,dict):
                time.sleep(10)
                continue
            job=reply.get("job")
            if not job:
                time.sleep(SLEEP_SECONDS)
                continue
            result_path=run_job(cfg,job)
            if result_path: deliver(cfg,result_path)
        except (OSError,URLError,TimeoutError,ValueError) as exc:
            print("Worker transport: "+type(exc).__name__,flush=True)
            time.sleep(10)

if __name__=="__main__":
    try: main()
    except (WorkerError,KeyboardInterrupt) as exc:
        print("S7 worker stopped: "+type(exc).__name__,file=sys.stderr,flush=True)
        sys.exit(1)
