"""Outbound-only executor. Run on a trusted PC with the shared worker secret."""
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from server.observability import package_versions

load_dotenv()
BASE = os.getenv('TTS_URL','http://127.0.0.1:8765').rstrip('/')
HEADERS = {'x-worker-key':os.environ['TTS_WORKER_KEY'],'x-edge-key':os.getenv('TTS_EDGE_KEY','')}
IDENTITY = os.getenv('TTS_WORKER_ID','notebook')
client = httpx.Client(base_url=BASE,headers=HEADERS,timeout=60)

ROOT = Path(__file__).resolve().parents[1]
MAX_MEDIA_BYTES = 90 * 1024**2
OUTBOX = Path(os.getenv('TTS_OUTBOX', str(ROOT / 'worker-outbox'))).resolve()

def _outbox_path(job):
    name = ''.join(c for c in job['id'] if c.isalnum() or c in '-_')[:80]
    if name != job['id'] or not name:
        raise ValueError('Invalid job identifier')
    digest = hashlib.sha256(job['lease'].encode()).hexdigest()[:20]
    return OUTBOX / f'{name}-{digest}.json'

def _atomic_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = path.with_name(path.name + '.' + os.urandom(6).hex() + '.tmp')
    data = json.dumps(value,ensure_ascii=False).encode('utf-8')
    fd = os.open(temp,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)

def _save_outbox(job, result):
    path = _outbox_path(job)
    _atomic_json(path,dict(id=job['id'],lease=job['lease'],expires=job['expires'],
                           payload=dict(result,lease=job['lease']),attempts=0,retry_at=0,stale=False))
    return path

def _deliver(path, now=None):
    """Only a positive API receipt can delete a completed result."""
    data = json.loads(path.read_text(encoding='utf-8'))
    now = time.time() if now is None else now
    if now >= data['expires']:
        path.unlink(missing_ok=True)  # Public retention has expired.
        return 'expired'
    if data.get('stale'):
        return 'stale'
    if data.get('dead_letter'):
        return 'quarantined'
    if data['retry_at']>now:
        return 'pending'
    try:
        remaining = max(1,min(120,data['expires']-now))
        response = client.post(f"/api/worker/{data['id']}/result",
                               json=data['payload'],timeout=remaining)
        if response.status_code==409:
            # Another lease or divergent completed result owns this job.
            data['stale']=True
            data['last_status']=409
            _atomic_json(path,data)
            return 'stale'
        # Permanent client errors cannot be repaired by retrying identical bytes.
        # Preserve the private payload for operator diagnosis, but do not starve
        # the entire worker queue for the remainder of the job's 24h TTL.
        transient = response.status_code in (408,425,429) or response.status_code>=500
        if response.status_code>=300 and not transient:
            data['dead_letter']=True
            data['dead_letter_status']=response.status_code
            data['dead_letter_at']=now
            _atomic_json(path,data)
            print(f"Delivery {data['id']}: quarantined HTTP {response.status_code}",flush=True)
            return 'quarantined'
        response.raise_for_status()
        try:
            ack=response.json()
        except ValueError:
            ack=None
        receipt=ack.get('receipt') if isinstance(ack,dict) and ack.get('ok') is True else None
        if not isinstance(receipt,str) or len(receipt)!=64 or any(c not in '0123456789abcdef' for c in receipt):
            # A malformed successful response cannot prove receipt durability.
            # Keep the result for review but avoid starving all other claims.
            data['dead_letter']=True
            data['dead_letter_status']=response.status_code
            data['dead_letter_reason']='invalid_receipt'
            data['dead_letter_at']=now
            _atomic_json(path,data)
            print(f"Delivery {data['id']}: quarantined invalid receipt",flush=True)
            return 'quarantined'
        path.unlink(missing_ok=True)
        return 'acked'
    except (httpx.HTTPError,ValueError,OSError) as error:
        data['attempts'] += 1
        data['retry_at'] = now + min(60,2 ** min(data['attempts'],6))
        _atomic_json(path,data)
        print(f"Delivery {data['id']}: {type(error).__name__}; retry queued",flush=True)
        return 'pending'

def _drain_outbox():
    OUTBOX.mkdir(parents=True,exist_ok=True)
    # A crash between fsync and rename can leave a private temporary spool.
    for temp in OUTBOX.glob('*.tmp'):
        try:
            if time.time()-temp.stat().st_mtime>86400:
                temp.unlink(missing_ok=True)
        except OSError:
            pass
    pending = False
    for path in sorted(OUTBOX.glob('*.json')):
        try:
            status = _deliver(path)
            if status == 'pending': pending = True
        except (ValueError,OSError,KeyError,TypeError) as exc:
            # Corrupt records require intervention, but must not permanently
            # prevent unrelated jobs from being claimed. Never discard a payload.
            print(f'Outbox {path.name}: quarantine inspection needed ({type(exc).__name__})',flush=True)
    return pending

def _stop_child(proc):
    if proc is None: return
    try:
        if proc.poll() is not None: return
    except BaseException:
        pass
    # The only process group targeted is the one created for this Popen handle.
    pid = getattr(proc,'pid',None)
    if pid and os.name == 'nt':
        try:
            subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],
                           capture_output=True,timeout=10,check=False)
        except (OSError,subprocess.TimeoutExpired):
            try: proc.kill()
            except OSError: pass
    elif pid:
        try: os.killpg(pid,signal.SIGTERM)
        except ProcessLookupError: pass
        except OSError: proc.terminate()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except (AttributeError,subprocess.TimeoutExpired):
        try:
            if pid and os.name != 'nt': os.killpg(pid,signal.SIGKILL)
            else: proc.kill()
        except (OSError,AttributeError): pass

def _qwen_models():
    runtime = os.getenv('TTS_QWEN_PYTHON','')
    if not runtime or not Path(runtime).is_file():
        return []
    if Path(runtime).resolve() == Path(sys.executable).resolve():
        return []  # Never mix Qwen's runtime into the Whisper environment.
    from server.qwen_engine import prepared_models
    return prepared_models()

def _engine_command(job,config):
    if job.get('engine','whisper') == 'whisper':
        return [sys.executable,'-m','server.infer',str(config)]
    if job['engine'] == 'qwen3':
        runtime = os.getenv('TTS_QWEN_PYTHON','')
        if not runtime or not Path(runtime).is_file() or Path(runtime).resolve()==Path(sys.executable).resolve():
            raise RuntimeError('QWEN_RUNTIME_UNAVAILABLE')
        if job['model'] not in _qwen_models():
            raise RuntimeError('QWEN_RECIPE_UNAVAILABLE')
        return [str(Path(runtime).resolve()),'-m','server.qwen_engine',str(config)]
    raise RuntimeError('ENGINE_UNSUPPORTED')

def execute(job):
    finished = threading.Event()
    lost = threading.Event()
    progress_file = None
    proc = None
    deadline = min(job['expires'],time.time()+7200)
    def check_budget():
        if lost.is_set() or time.time() >= deadline:
            raise RuntimeError('Tempo de preparação/execução ou lease expirou.')
    def heartbeat():
        while not finished.wait(15):
            try:
                progress={'progress':0,'stage':'preparing'}
                if progress_file and progress_file.exists():
                    try: progress=json.loads(progress_file.read_text(encoding='utf-8'))
                    except (ValueError,OSError): pass
                r = client.post(f"/api/worker/{job['id']}/heartbeat",
                                json={'lease':job['lease'],**progress})
                if r.status_code==409: lost.set()
            except httpx.HTTPError:
                pass
    thread = threading.Thread(target=heartbeat,daemon=True)
    thread.start()
    try:
        check_budget()
        with tempfile.TemporaryDirectory(prefix='tts-',dir=os.getenv('TTS_TMP')) as temp:
            folder = Path(temp)
            progress_file=folder/'progress.json'
            config = folder/'job.json'
            config.write_text(json.dumps(job),encoding='utf-8')
            if job['source']=='upload':
                check_budget()
                timeout = httpx.Timeout(max(1,min(30,deadline-time.time())))
                with client.stream('GET',f"/api/worker/{job['id']}/input",
                                   headers={'x-lease':job['lease']},timeout=timeout) as r:
                    r.raise_for_status()
                    with (folder/'input.media').open('wb') as stream:
                        transferred = 0
                        for chunk in r.iter_bytes():
                            check_budget()
                            transferred += len(chunk)
                            if transferred > MAX_MEDIA_BYTES:
                                raise RuntimeError('Input exceeded media size budget')
                            stream.write(chunk)
                            check_budget()
            check_budget()  # Nothing may spawn after a slow or expired download.
            flags = {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
            proc = subprocess.Popen(_engine_command(job,config),cwd=ROOT,**flags)
            while proc.poll() is None:
                check_budget()
                time.sleep(min(1,max(0.01,deadline-time.time())))
            check_budget()
            output = folder/'result.json'
            if not output.exists(): raise RuntimeError('Executor finalizou sem resultado.')
            result = json.loads(output.read_text(encoding='utf-8'))
            if not isinstance(result,dict):
                raise ValueError('Invalid engine result')
            if not result.get('error'):
                result.setdefault('result_state','complete' if result.get('segments') else 'unknown')
            path = _save_outbox(job,result)  # Durable before the temp folder is removed.
            _deliver(path)
    except Exception as error:
        print(f"Job {job['id']}: {type(error).__name__}",flush=True)
        if not lost.is_set() and time.time() < job['expires']:
            # Processing failed: report failure. Transport errors never enter here.
            try:
                path = _save_outbox(job,{'error':'Falha no executor. Tente outro arquivo ou avise o administrador.'})
                _deliver(path)
            except (OSError,ValueError) as exc:
                print(f"Job {job['id']}: unable to persist failure: {type(exc).__name__}",flush=True)
    finally:
        _stop_child(proc)
        finished.set()
        thread.join(timeout=2)

def main():
    print(f'Executor {IDENTITY} conectado a {BASE}',flush=True)
    while True:
        try:
            if _drain_outbox():
                time.sleep(5)
                continue
            r=client.post('/api/worker/claim',json={'id':IDENTITY,'info':{'system':platform.system(),'machine':platform.machine(),'cpu':os.cpu_count(),'engine':'faster-whisper','qwen_models':_qwen_models(),'versions':package_versions()}})
            r.raise_for_status()
            job=r.json()['job']
            if job: execute(job)
            else: time.sleep(5)
        except (httpx.HTTPError,ValueError) as e:
            print(f'Conexão: {type(e).__name__}; nova tentativa em 10s',flush=True)
            time.sleep(10)

if __name__=='__main__': main()
