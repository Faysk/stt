"""Outbound-only executor. Run on a trusted PC with the shared worker secret."""
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()
BASE = os.getenv('TTS_URL','http://127.0.0.1:8765').rstrip('/')
HEADERS = {'x-worker-key':os.environ['TTS_WORKER_KEY'],'x-edge-key':os.getenv('TTS_EDGE_KEY','')}
IDENTITY = os.getenv('TTS_WORKER_ID','notebook')
client = httpx.Client(base_url=BASE,headers=HEADERS,timeout=60)

def execute(job):
    finished = threading.Event()
    lost = threading.Event()
    def heartbeat():
        while not finished.wait(15):
            try:
                r = client.post(f"/api/worker/{job['id']}/heartbeat",json={'lease':job['lease']})
                if r.status_code==409: lost.set()
            except httpx.HTTPError:
                pass
    thread = threading.Thread(target=heartbeat,daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='tts-', dir=os.getenv('TTS_TMP')) as temp:
            folder = Path(temp)
            config = folder/'job.json'
            config.write_text(json.dumps(job),encoding='utf-8')
            if job['source']=='upload':
                with client.stream('GET',f"/api/worker/{job['id']}/input",headers={'x-lease':job['lease']}) as r:
                    r.raise_for_status()
                    with (folder/'input.media').open('wb') as f:
                        for chunk in r.iter_bytes(): f.write(chunk)
            proc = subprocess.Popen([sys.executable,'-m','server.infer',str(config)],cwd=Path(__file__).resolve().parents[1])
            deadline = min(time.time()+7200,job['expires'])
            while proc.poll() is None:
                if lost.is_set() or time.time()>deadline:
                    proc.kill()
                    proc.wait()
                    raise RuntimeError('Tempo de execução ou lease expirou.')
                time.sleep(1)
            output = folder/'result.json'
            if not output.exists(): raise RuntimeError('Executor finalizou sem resultado.')
            result = json.loads(output.read_text(encoding='utf-8'))
            result['lease']=job['lease']
            r = client.post(f"/api/worker/{job['id']}/result",json=result,timeout=120)
            r.raise_for_status()
    except Exception as e:
        print(f"Job {job['id']}: {type(e).__name__}",flush=True)
        try:
            client.post(f"/api/worker/{job['id']}/result",json={'lease':job['lease'],'error':'Falha no executor. Tente outro arquivo ou avise o administrador.'}).raise_for_status()
        except httpx.HTTPError: pass
    finally:
        finished.set()
        thread.join(timeout=2)

def main():
    print(f'Executor {IDENTITY} conectado a {BASE}',flush=True)
    while True:
        try:
            r=client.post('/api/worker/claim',json={'id':IDENTITY,'info':{'system':platform.system(),'machine':platform.machine(),'cpu':os.cpu_count(),'engine':'faster-whisper'}})
            r.raise_for_status()
            job=r.json()['job']
            if job: execute(job)
            else: time.sleep(5)
        except (httpx.HTTPError,ValueError) as e:
            print(f'Conexão: {type(e).__name__}; nova tentativa em 10s',flush=True)
            time.sleep(10)

if __name__=='__main__': main()
