import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import threading
import time
import tempfile
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('TTS_DATA', str(ROOT))).resolve()
BLOB = DATA / 'blob'
DB = DATA / 'db' / 'tts.sqlite3'
MAX_BYTES = 90 * 1024 * 1024
TTL = 86400
MODELS = ['tiny', 'base', 'small', 'medium', 'large-v3', 'turbo']

@contextmanager
def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def init():
    BLOB.mkdir(parents=True, exist_ok=True)
    (BLOB/'.tmp').mkdir(exist_ok=True)
    tempfile.tempdir = str(BLOB/'.tmp')
    DB.parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT, source TEXT,
          status TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
          started REAL, finished REAL, worker TEXT, lease TEXT, heartbeat REAL,
          model TEXT, device TEXT, compute TEXT, error TEXT, input_bytes INTEGER DEFAULT 0,
          duration REAL, elapsed REAL, cpu_seconds REAL, peak_rss_mb REAL, peak_vram_mb REAL,
          language TEXT, progress REAL DEFAULT 0, attempts INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner, created);
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, seen REAL, info TEXT);
        CREATE TABLE IF NOT EXISTS admission (client TEXT, created REAL);
        ''')
        c.execute('INSERT OR IGNORE INTO settings VALUES (1, ?)', (json.dumps(dict(worker='notebook', model='small', device='cpu', compute='int8', paused=False)),))

def maintenance():
    now = time.time()
    temp_root=(BLOB/'.tmp').resolve()
    if temp_root.exists():
        for path in temp_root.iterdir():
            if path.resolve().parent==temp_root and now-path.stat().st_mtime>TTL:
                if path.is_dir(): shutil.rmtree(path,ignore_errors=True)
                else: path.unlink(missing_ok=True)
    with db() as c:
        rows = c.execute("SELECT id FROM jobs WHERE expires <= ? AND status != 'expired'", (now,)).fetchall()
        for r in rows:
            shutil.rmtree(BLOB / r['id'], ignore_errors=True)
        c.execute("UPDATE jobs SET status='expired',owner='',title=NULL,source=NULL,error=NULL,lease=NULL,language=NULL WHERE expires<=?", (now,))
        c.execute("UPDATE jobs SET status=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END, error='Executor desconectado; limite de tentativas atingido.',lease=NULL WHERE status='running' AND heartbeat<? AND expires>?", (now-180, now))
        stale = c.execute("SELECT id FROM jobs WHERE status='uploading' AND created<?",(now-900,)).fetchall()
        for row in stale: shutil.rmtree(BLOB/row['id'],ignore_errors=True)
        c.execute("UPDATE jobs SET status='failed',error='Envio interrompido.' WHERE status='uploading' AND created<?",(now-900,))
        c.execute('DELETE FROM admission WHERE created<?',(now-3600,))

stop = threading.Event()
def janitor():
    while not stop.wait(30):
        maintenance()

@asynccontextmanager
async def lifespan(app):
    init()
    maintenance()
    stop.clear()
    thread = threading.Thread(target=janitor, daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=2)

app = FastAPI(title='Faysk Transcription Lab', lifespan=lifespan)

@app.middleware('http')
async def guard(request: Request, call_next):
    length = request.headers.get('content-length')
    if length and (not length.isdecimal() or int(length)>MAX_BYTES+1024*1024):
        return JSONResponse({'detail':'Limite de 90 MB por arquivo.'}, status_code=413)
    if os.getenv('TTS_REQUIRE_EDGE') == '1':
        key = os.getenv('TTS_EDGE_KEY', '')
        if not key or not hmac.compare_digest(request.headers.get('x-edge-key', ''), key):
            return JSONResponse({'detail':'Origem não autorizada.'}, status_code=403)
    if request.url.path in ('/api/jobs/upload','/api/jobs/youtube') and request.method=='POST':
        token=request.headers.get('x-session-key','')
        if len(token)!=64 or any(ch not in '0123456789abcdef' for ch in token):
            return JSONResponse({'detail':'Sessão inválida.'},status_code=401)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response

def secret(request, env, header):
    expected = os.getenv(env, '')
    if not expected or not hmac.compare_digest(request.headers.get(header, ''), expected):
        raise HTTPException(401, 'Chave inválida.')

def admin(request: Request):
    secret(request, 'TTS_ADMIN_KEY', 'x-admin-key')

def worker(request: Request):
    secret(request, 'TTS_WORKER_KEY', 'x-worker-key')

def owner(request: Request):
    token = request.headers.get('x-session-key', '')
    if len(token) != 64 or any(x not in '0123456789abcdef' for x in token):
        raise HTTPException(401, 'Sessão inválida.')
    return hashlib.sha256(token.encode()).hexdigest()

def admit(request: Request):
    ip = request.headers.get('x-tts-client') if os.getenv('TTS_REQUIRE_EDGE')=='1' else request.client.host
    key = hashlib.sha256((os.getenv('TTS_EDGE_KEY','local')+(ip or 'unknown')).encode()).hexdigest()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT count(*) FROM admission WHERE client=? AND created>?',(key,time.time()-3600)).fetchone()[0]>=20:
            raise HTTPException(429,'Limite de envios desta rede atingido. Tente mais tarde.')
        c.execute('INSERT INTO admission VALUES (?,?)',(key,time.time()))

def public(r):
    return {k:r[k] for k in ('id','title','status','created','expires','model','worker','error','duration','elapsed','language','progress')}

def owned(job_id, own):
    with db() as c:
        r = c.execute('SELECT * FROM jobs WHERE id=? AND owner=? AND expires>?', (job_id, own, time.time())).fetchone()
    if not r:
        raise HTTPException(404, 'Transcrição não encontrada ou expirada.')
    return r

def new_job(c, own, title, source):
    now = time.time()
    # Serialize admission checks and insertion, including uploads in flight.
    c.execute('BEGIN IMMEDIATE')
    if c.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','uploading','running')").fetchone()[0] >= 20:
        raise HTTPException(429, 'Fila cheia. Tente novamente mais tarde.')
    if c.execute('SELECT count(*) FROM jobs WHERE owner=? AND created>?', (own, now-3600)).fetchone()[0] >= 10:
        raise HTTPException(429, 'Limite de 10 envios por hora nesta sessão.')
    if shutil.disk_usage(BLOB).free < 2 * 1024**3:
        raise HTTPException(503, 'Armazenamento temporariamente indisponível.')
    jid = secrets.token_hex(16)
    c.execute("INSERT INTO jobs (id,owner,title,source,status,created,expires) VALUES (?,?,?,?,'uploading',?,?)", (jid,own,title,source,now,now+TTL))
    return jid

class BodyLimit:
    def __init__(self, app): self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        size=0
        async def limited():
            nonlocal size
            message=await receive()
            size+=len(message.get('body',b''))
            if size>MAX_BYTES+1024*1024:
                raise HTTPException(413,'Limite de 90 MB por arquivo.')
            return message
        await self.app(scope,limited,send)

app.add_middleware(BodyLimit)

@app.get('/api/health')
def health():
    with db() as c:
        queue = c.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        cfg = json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
        recent = c.execute('SELECT count(*) FROM workers WHERE seen>? AND id=?', (time.time()-90,cfg['worker'])).fetchone()[0]
    return dict(online=bool(recent), queue=queue, paused=cfg['paused'], max_mb=90, retention_hours=24)

@app.get('/api/jobs')
def jobs(own=Depends(owner)):
    with db() as c:
        return [public(r) for r in c.execute('SELECT * FROM jobs WHERE owner=? AND expires>? ORDER BY created DESC', (own,time.time()))]

@app.post('/api/jobs/upload', status_code=201, dependencies=[Depends(admit)])
async def upload(file: UploadFile = File(...), own=Depends(owner)):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.mp3','.wav','.m4a','.mp4','.webm','.ogg','.flac','.aac','.mov','.mkv','.opus'}:
        raise HTTPException(400, 'Formato de áudio ou vídeo não suportado.')
    with db() as c:
        jid = new_job(c, own, Path(file.filename).name[:180], 'upload')
    folder = BLOB / jid
    folder.mkdir()
    size = 0
    try:
        with (folder / 'input.media').open('wb') as f:
            while chunk := await file.read(1024*1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, 'Limite de 90 MB por arquivo.')
                f.write(chunk)
        if not size:
            raise HTTPException(400, 'Arquivo vazio.')
        with db() as c:
            c.execute("UPDATE jobs SET status='queued',input_bytes=? WHERE id=?", (size,jid))
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        with db() as c:
            c.execute('DELETE FROM jobs WHERE id=?', (jid,))
        raise
    return {'id':jid}

class Link(BaseModel):
    url: str = Field(max_length=2048)

def youtube_url(value):
    try:
        u = urlparse(value)
        port = u.port
    except ValueError:
        raise HTTPException(400,'Link inválido.')
    if u.scheme != 'https' or u.username or u.password or port not in (None,443):
        raise HTTPException(400, 'Use um link HTTPS do YouTube.')
    video = ''
    if u.hostname in ('youtube.com','www.youtube.com','m.youtube.com'):
        if u.path == '/watch': video = parse_qs(u.query).get('v',[''])[0]
        elif u.path.startswith(('/shorts/','/live/')): video = u.path.split('/')[2]
    elif u.hostname == 'youtu.be': video = u.path.lstrip('/')
    if len(video) != 11 or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for ch in video):
        raise HTTPException(400, 'Link de vídeo do YouTube inválido. Playlists não são aceitas.')
    return 'https://www.youtube.com/watch?v=' + video

@app.post('/api/jobs/youtube', status_code=201, dependencies=[Depends(admit)])
def youtube(link: Link, own=Depends(owner)):
    url = youtube_url(link.url)
    with db() as c:
        jid = new_job(c, own, 'Vídeo do YouTube', url)
        c.execute("UPDATE jobs SET status='queued' WHERE id=?", (jid,))
    (BLOB / jid).mkdir()
    return {'id':jid}

@app.get('/api/jobs/{jid}/download/{fmt}')
def download(jid: str, fmt: str, own=Depends(owner)):
    r = owned(jid,own)
    if fmt not in ('txt','md','srt','json') or r['status'] != 'done':
        raise HTTPException(404, 'Resultado indisponível.')
    return FileResponse(BLOB / jid / ('transcript.'+fmt), filename='transcricao.'+fmt, media_type='application/json' if fmt=='json' else 'text/plain; charset=utf-8')

@app.get('/api/jobs/{jid}')
def job(jid: str, own=Depends(owner)):
    r = owned(jid,own)
    result = public(r)
    if r['status']=='done':
        result['text'] = (BLOB / jid / 'transcript.txt').read_text(encoding='utf-8')
    return result

class Settings(BaseModel):
    worker: str = Field(min_length=1,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
    model: str
    device: str
    compute: str
    paused: bool = False

@app.get('/api/admin', dependencies=[Depends(admin)])
def dashboard():
    import psutil
    with db() as c:
        return dict(settings=json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0]),
          workers=[dict(r) | {'info':json.loads(r['info'])} for r in c.execute('SELECT * FROM workers')],
          jobs=[dict(r) for r in c.execute('SELECT id,status,created,model,worker,device,compute,duration,elapsed,cpu_seconds,peak_rss_mb,peak_vram_mb,input_bytes FROM jobs ORDER BY created DESC LIMIT 200')],
          totals=dict(c.execute("SELECT count(*) jobs,coalesce(sum(duration),0) audio_seconds,coalesce(sum(elapsed),0) processing_seconds,coalesce(sum(input_bytes),0) bytes FROM jobs").fetchone()),
          host=dict(cpu_percent=psutil.cpu_percent(),ram_percent=psutil.virtual_memory().percent,free_gb=round(shutil.disk_usage(BLOB).free/1024**3,1)), models=MODELS)

@app.put('/api/admin/settings', dependencies=[Depends(admin)])
def settings(s: Settings):
    if s.model not in MODELS or (s.device,s.compute) not in [('cpu','int8'),('cpu','float32'),('cuda','float16'),('cuda','int8_float16')]:
        raise HTTPException(400,'Combinação de modelo, dispositivo ou precisão inválida.')
    with db() as c:
        c.execute('UPDATE settings SET value=? WHERE id=1', (s.model_dump_json(),))
    return s

class Claim(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    info: dict = Field(default_factory=dict)

@app.post('/api/worker/claim', dependencies=[Depends(worker)])
def claim(w: Claim):
    now = time.time()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT OR REPLACE INTO workers VALUES (?,?,?)', (w.id,now,json.dumps(w.info)[:8000]))
        cfg = json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
        if cfg['paused'] or cfg['worker'] != w.id:
            return {'job':None}
        if c.execute("SELECT 1 FROM jobs WHERE status='running' AND worker=?",(w.id,)).fetchone():
            return {'job':None}
        r = c.execute("SELECT * FROM jobs WHERE status='queued' AND expires>? ORDER BY created LIMIT 1", (now,)).fetchone()
        if not r: return {'job':None}
        lease = secrets.token_hex(32)
        c.execute("UPDATE jobs SET status='running',started=?,worker=?,model=?,device=?,compute=?,lease=?,heartbeat=?,attempts=attempts+1,error=NULL WHERE id=?", (now,w.id,cfg['model'],cfg['device'],cfg['compute'],lease,now,r['id']))
        return {'job':dict(id=r['id'],source=r['source'],lease=lease,expires=r['expires'],**cfg)}

def leased(c,jid,lease):
    r = c.execute("SELECT * FROM jobs WHERE id=? AND lease=? AND status='running' AND expires>?", (jid,lease,time.time())).fetchone()
    if not r: raise HTTPException(409,'Lease expirado.')
    return r

@app.get('/api/worker/{jid}/input', dependencies=[Depends(worker)])
def worker_input(jid: str, request: Request):
    with db() as c: leased(c,jid,request.headers.get('x-lease',''))
    p = BLOB / jid / 'input.media'
    if not p.exists(): raise HTTPException(404,'Entrada indisponível.')
    return FileResponse(p)

class Heartbeat(BaseModel):
    lease: str
    progress: float = Field(default=0,ge=0,le=100)

@app.post('/api/worker/{jid}/heartbeat', dependencies=[Depends(worker)])
def heartbeat(jid: str, h: Heartbeat):
    with db() as c:
        r = leased(c,jid,h.lease)
        c.execute('UPDATE jobs SET heartbeat=?,progress=? WHERE id=?',(time.time(),h.progress,jid))
        c.execute('UPDATE workers SET seen=? WHERE id=?',(time.time(),r['worker']))
    return {'ok':True}

class Segment(BaseModel):
    start: float = Field(ge=0,le=7200)
    end: float = Field(ge=0,le=7200)
    text: str = Field(max_length=10000)

class Result(BaseModel):
    lease: str
    segments: list[Segment] = Field(default_factory=list,max_length=30000)
    language: str = Field(default='',max_length=20)
    duration: float = Field(default=0,ge=0,le=7200)
    elapsed: float = Field(default=0,ge=0)
    cpu_seconds: float = Field(default=0,ge=0)
    peak_rss_mb: float = Field(default=0,ge=0)
    peak_vram_mb: float = Field(default=0,ge=0)
    input_bytes: int = Field(default=0,ge=0)
    error: str | None = Field(default=None,max_length=500)

def timestamp(n):
    ms = int(n*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

@app.post('/api/worker/{jid}/result', dependencies=[Depends(worker)])
def result(jid: str, result: Result):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        r = leased(c,jid,result.lease)
        if not result.error:
            folder = BLOB / jid
            folder.mkdir(exist_ok=True)
            text = '\n'.join(s.text.strip() for s in result.segments)
            (folder/'transcript.txt').write_text(text,encoding='utf-8')
            (folder/'transcript.md').write_text('# Transcrição\n\n'+text,encoding='utf-8')
            (folder/'transcript.srt').write_text('\n\n'.join(f'{i}\n{timestamp(s.start)} --> {timestamp(s.end)}\n{s.text.strip()}' for i,s in enumerate(result.segments,1)),encoding='utf-8')
            (folder/'transcript.json').write_text(json.dumps(dict(language=result.language,duration=result.duration,model=r['model'],segments=[s.model_dump() for s in result.segments]),ensure_ascii=False),encoding='utf-8')
        c.execute('UPDATE jobs SET status=?,finished=?,duration=?,elapsed=?,cpu_seconds=?,peak_rss_mb=?,peak_vram_mb=?,language=?,error=?,progress=?,lease=NULL,input_bytes=max(input_bytes,?) WHERE id=?', ('failed' if result.error else 'done',time.time(),result.duration,result.elapsed,result.cpu_seconds,result.peak_rss_mb,result.peak_vram_mb,result.language,result.error,0 if result.error else 100,result.input_bytes,jid))
    return {'ok':True}

app.mount('/', StaticFiles(directory=ROOT/'web',html=True),name='web')
