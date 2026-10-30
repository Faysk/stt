import hashlib
import hmac
import json
import math
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
from pydantic import BaseModel, Field, model_validator
from server.catalog import MODEL_CATALOG, installed_models
from server import observability as obs
from server.metrics import validate as validate_metrics

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
        CREATE TABLE IF NOT EXISTS upload_slots (id TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL);
        ''')
        columns={r[1] for r in c.execute('PRAGMA table_info(jobs)')}
        for name,definition in {
            'client_tag':'TEXT','network_tag':'TEXT','ui_language':'TEXT',
            'priority':'INTEGER DEFAULT 0','outcome':'TEXT','runtime':'TEXT',
            'model_revision':'TEXT','stage':"TEXT DEFAULT 'queued'",
            'purged_at':'REAL','purge_retry_at':'REAL DEFAULT 0','purge_attempts':'INTEGER DEFAULT 0',
            'purge_error':'TEXT','receipt_hash':'TEXT','receipt_lease':'TEXT','result_state':'TEXT','metrics_json':'TEXT','engine':'TEXT'}.items():
            if name not in columns: c.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
        c.execute("UPDATE jobs SET outcome=status WHERE outcome IS NULL AND status IN ('done','failed','cancelled')")
        c.execute("UPDATE jobs SET stage=status WHERE stage='queued' AND status IN ('done','failed','expired','cancelled')")
        # Backfill an anonymous administrative label; this is not an access credential.
        for row in c.execute("SELECT id,owner FROM jobs WHERE client_tag IS NULL AND owner!=''").fetchall():
            c.execute('UPDATE jobs SET client_tag=? WHERE id=?',(client_tag(row['owner']),row['id']))
        obs.install(c)
        c.execute('INSERT OR IGNORE INTO settings VALUES (1, ?)', (json.dumps(dict(worker='notebook', model='small', device='cpu', compute='int8', paused=False)),))

def _purge_job(c, jid, now):
    # The physical deletion receipt is independent of public expiry. Retry after
    # restart or transient file-lock errors, including already-expired legacy rows.
    if len(jid) != 32 or any(ch not in '0123456789abcdef' for ch in jid):
        c.execute("UPDATE jobs SET purge_error=?,purge_retry_at=? WHERE id=?",
                  ('invalid job directory identifier', now + 3600, jid))
        return
    folder = BLOB / jid
    try:
        if folder.is_symlink():
            raise OSError('Refusing to follow job directory symlink')
        if folder.exists():
            shutil.rmtree(folder)
        if folder.exists() or folder.is_symlink():
            raise OSError('Job directory still exists after deletion')
    except OSError as error:
        attempt = c.execute('SELECT purge_attempts FROM jobs WHERE id=?', (jid,)).fetchone()[0] + 1
        delay = min(3600, 15 * 2 ** min(attempt - 1, 8))
        c.execute("""UPDATE jobs SET purge_attempts=?,purge_retry_at=?,purge_error=?
                     WHERE id=?""", (attempt, now + delay, type(error).__name__, jid))
    else:
        c.execute("""UPDATE jobs SET purged_at=?,purge_retry_at=0,purge_error=NULL
                     WHERE id=?""", (now, jid))

def maintenance():
    now = time.time()
    temp_root = (BLOB / '.tmp').resolve()
    if temp_root.exists():
        for path in temp_root.iterdir():
            try:
                if path.is_symlink():
                    # Never follow symlinks outside the managed temporary root.
                    path.unlink()
                elif path.resolve().parent == temp_root and now - path.stat().st_mtime > TTL:
                    if path.is_dir(): shutil.rmtree(path)
                    else: path.unlink(missing_ok=True)
            except OSError:
                # A locked temporary file remains eligible on the next pass.
                continue
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute("""UPDATE jobs SET status='expired',stage='expired',owner='',
                  title=NULL,source=NULL,error=NULL,lease=NULL,language=NULL
                  WHERE expires<=? AND status!='expired'""", (now,))
        rows = c.execute("""SELECT id FROM jobs WHERE expires<=? AND purged_at IS NULL
                            AND coalesce(purge_retry_at,0)<=?""", (now,now)).fetchall()
        for row in rows:
            _purge_job(c,row['id'],now)
        for row in c.execute("SELECT id,attempts FROM jobs WHERE status='running' AND heartbeat<? AND expires>?",(now-180,now)).fetchall():
            failed=row['attempts']>=3
            c.execute("UPDATE jobs SET status=?,outcome=?,stage=?,error=?,lease=NULL WHERE id=?",('failed' if failed else 'queued','failed' if failed else None,'failed' if failed else 'queued','Executor desconectado; limite de tentativas atingido.' if failed else None,row['id']))
            c.execute('UPDATE jobs SET finished=? WHERE id=?',(now if failed else None,row['id']))
            obs.event(c,row['id'],'failed' if failed else 'requeued',data={'reason':'heartbeat_timeout'})
        stale = c.execute("SELECT id FROM jobs WHERE status='uploading' AND created<?",(now-900,)).fetchall()
        for row in stale:
            # Stale media is also covered by the ordinary expiry/purge pass.
            try: shutil.rmtree(BLOB / row['id'])
            except FileNotFoundError: pass
            except OSError: pass
            obs.event(c,row['id'],'failed',data={'reason':'upload_timeout'})
        c.execute("UPDATE jobs SET status='failed',outcome='failed',stage='failed',finished=?,error='Envio interrompido.' WHERE status='uploading' AND created<?",(now,now-900))
        c.execute('DELETE FROM admission WHERE created<?',(now-3600,))
        c.execute('DELETE FROM upload_slots WHERE created<?',(now-7200,))

stop = threading.Event()
host_snapshot={}
telemetry_error=None
def collect():
    global host_snapshot,telemetry_error
    import psutil
    gpu=obs.gpu_info()
    ram=psutil.virtual_memory()
    disk=shutil.disk_usage(BLOB)
    host_snapshot=dict(at=time.time(),cpu_percent=psutil.cpu_percent(interval=.1),ram_percent=ram.percent,
        ram_used_gb=round(ram.used/1024**3,2),ram_total_gb=round(ram.total/1024**3,2),
        free_gb=round(disk.free/1024**3,1),total_gb=round(disk.total/1024**3,1),gpu=gpu)
    with db() as c:
        queue=c.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0]
        obs.record(c,'api-host',host_snapshot['cpu_percent'],ram.percent,gpu['utilization'] if gpu else None,host_snapshot['free_gb'],queue)
    telemetry_error=None

def janitor():
    global telemetry_error
    while not stop.is_set():
        try:
            maintenance()
            collect()
        except Exception as error:
            telemetry_error=type(error).__name__
        if stop.wait(15): break

@asynccontextmanager
async def lifespan(app):
    init()
    maintenance()
    stop.clear()
    thread = threading.Thread(target=janitor, daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=5)

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
    slot = None
    try:
        slot = preflight(request)
    except HTTPException as exc:
        return JSONResponse({'detail':exc.detail},status_code=exc.status_code)
    request.state.upload_slot = slot
    try:
        response = await call_next(request)
    finally:
        if slot:
            with db() as c:
                c.execute('DELETE FROM upload_slots WHERE id=?',(slot,))
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if request.url.path.startswith('/api/') or request.url.path=='/admin.html':
        response.headers['X-Robots-Tag']='noindex, nofollow, noarchive'
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

def preflight(request: Request):
    """Admission runs in HTTP middleware, BEFORE Starlette reads multipart bytes."""
    if request.method != 'POST' or request.url.path not in ('/api/jobs/upload','/api/jobs/youtube'):
        return None
    session = request.headers['x-session-key']
    own = hashlib.sha256(session.encode()).hexdigest()
    ip = request.headers.get('x-tts-client') if os.getenv('TTS_REQUIRE_EDGE')=='1' else (request.client.host if request.client else None)
    key = hashlib.sha256((os.getenv('TTS_EDGE_KEY','local') + (ip or 'unknown')).encode()).hexdigest()
    now = time.time()
    is_upload = request.url.path.endswith('/upload')
    slot = secrets.token_hex(16) if is_upload else None
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM upload_slots WHERE created<?',(now-7200,))
        if c.execute('SELECT count(*) FROM admission WHERE client=? AND created>?',(key,now-3600)).fetchone()[0] >= 20:
            raise HTTPException(429,'Limite de envios desta rede atingido. Tente mais tarde.')
        active = c.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','uploading','running')").fetchone()[0]
        inflight = c.execute('SELECT count(*) FROM upload_slots').fetchone()[0]
        if active + inflight >= 20:
            raise HTTPException(429,'Fila cheia. Tente novamente mais tarde.')
        if c.execute('SELECT count(*) FROM jobs WHERE owner=? AND created>?',(own,now-3600)).fetchone()[0] >= 10:
            raise HTTPException(429,'Limite de 10 envios por hora nesta sessão.')
        if is_upload and inflight >= 4:
            raise HTTPException(429,'Muitos uploads simultâneos. Tente novamente.')
        if shutil.disk_usage(BLOB).free < 2 * 1024**3:
            raise HTTPException(503,'Armazenamento temporariamente indisponível.')
        c.execute('INSERT INTO admission VALUES (?,?)',(key,now))
        if slot:
            c.execute('INSERT INTO upload_slots VALUES (?,?,?)',(slot,own,now))
    return slot

def client_tag(own):
    return hmac.new(os.getenv('TTS_ADMIN_KEY','local').encode(),own.encode(),'sha256').hexdigest()[:16]

def public(r):
    result={k:r[k] for k in ('id','title','status','created','expires','started','finished','error','duration','elapsed','language','progress','stage')}
    if r['status']=='queued':
        with db() as c:
            result['position']=1+c.execute("SELECT count(*) FROM jobs WHERE status='queued' AND expires>? AND (priority>? OR (priority=? AND (created<? OR (created=? AND id<?))))",(time.time(),r['priority'],r['priority'],r['created'],r['created'],r['id'])).fetchone()[0]
    return result

def owned(job_id, own):
    with db() as c:
        r = c.execute('SELECT * FROM jobs WHERE id=? AND owner=? AND expires>?', (job_id, own, time.time())).fetchone()
    if not r:
        raise HTTPException(404, 'Transcrição não encontrada ou expirada.')
    return r

def new_job(c, own, title, source, request=None):
    now = time.time()
    # Serialize admission checks and insertion, including uploads in flight.
    c.execute('BEGIN IMMEDIATE')
    slot = getattr(request.state, 'upload_slot', None) if request else None
    if slot:
        c.execute('DELETE FROM upload_slots WHERE id=? AND owner=?', (slot,own))
    active = c.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','uploading','running')").fetchone()[0]
    inflight = c.execute('SELECT count(*) FROM upload_slots').fetchone()[0]
    if active + inflight >= 20:
        raise HTTPException(429, 'Fila cheia. Tente novamente mais tarde.')
    if c.execute('SELECT count(*) FROM jobs WHERE owner=? AND created>?', (own, now-3600)).fetchone()[0] >= 10:
        raise HTTPException(429, 'Limite de 10 envios por hora nesta sessão.')
    if shutil.disk_usage(BLOB).free < 2 * 1024**3:
        raise HTTPException(503, 'Armazenamento temporariamente indisponível.')
    jid = secrets.token_hex(16)
    c.execute("INSERT INTO jobs (id,owner,title,source,status,created,expires) VALUES (?,?,?,?,'uploading',?,?)", (jid,own,title,source,now,now+TTL))
    ip=request.headers.get('x-tts-client') if request and os.getenv('TTS_REQUIRE_EDGE')=='1' else None
    network=hmac.new(os.getenv('TTS_EDGE_KEY','local').encode(),ip.encode(),'sha256').hexdigest()[:16] if ip else None
    lang=request.headers.get('x-ui-language','en')[:12] if request else 'en'
    c.execute('UPDATE jobs SET client_tag=?,network_tag=?,ui_language=? WHERE id=?',(client_tag(own),network,lang,jid))
    obs.event(c,jid,'submitted',actor='session:'+client_tag(own),data={'source':'upload' if source=='upload' else 'youtube'})
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
        waiting=c.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0]
        running=c.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]
    pressure='free' if queue==0 else 'short' if queue<=3 else 'medium' if queue<=10 else 'high'
    return dict(online=bool(recent), queue=queue,waiting=waiting,running=running,pressure=pressure,paused=cfg['paused'],max_mb=90, retention_hours=24)

@app.get('/api/jobs')
def jobs(own=Depends(owner)):
    with db() as c:
        return [public(r) for r in c.execute('SELECT * FROM jobs WHERE owner=? AND expires>? ORDER BY created DESC', (own,time.time()))]

@app.post('/api/jobs/upload', status_code=201)
async def upload(request: Request, file: UploadFile = File(...), own=Depends(owner)):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.mp3','.wav','.m4a','.mp4','.webm','.ogg','.flac','.aac','.mov','.mkv','.opus'}:
        raise HTTPException(400, 'Formato de áudio ou vídeo não suportado.')
    with db() as c:
        jid = new_job(c, own, Path(file.filename).name[:180], 'upload',request)
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

@app.post('/api/jobs/youtube', status_code=201)
def youtube(link: Link, request: Request, own=Depends(owner)):
    url = youtube_url(link.url)
    with db() as c:
        jid = new_job(c, own, 'YouTube', url,request)
        c.execute("UPDATE jobs SET status='queued' WHERE id=?", (jid,))
    (BLOB / jid).mkdir()
    return {'id':jid}

@app.get('/api/jobs/{jid}/download/{fmt}')
def download(jid: str, fmt: str, own=Depends(owner)):
    r = owned(jid,own)
    if fmt not in ('txt','md','srt','json') or r['status'] != 'done':
        raise HTTPException(404, 'Resultado indisponível.')
    path = BLOB / jid / ('transcript.'+fmt)
    if not path.is_file(): raise HTTPException(404, 'Formato indisponível para este motor.')
    return FileResponse(path, filename='transcricao.'+fmt, media_type='application/json' if fmt=='json' else 'text/plain; charset=utf-8')

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
    engine: str = 'whisper'
    language_hint: str = Field(default='auto',min_length=2,max_length=32)

@app.get('/api/admin', dependencies=[Depends(admin)])
def dashboard():
    with db() as c:
        return dict(settings=json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0]),
          workers=[dict(r) | {'info':json.loads(r['info'])} for r in c.execute('SELECT * FROM workers')],
          jobs=[admin_job(r) for r in c.execute("SELECT * FROM jobs WHERE status IN ('queued','running','uploading') ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END,priority DESC,created,id")],
          counts={r['status']:r['n'] for r in c.execute('SELECT status,count(*) n FROM jobs GROUP BY status')},
          totals=dict(c.execute("SELECT count(*) jobs,coalesce(sum(duration),0) audio_seconds,coalesce(sum(elapsed),0) processing_seconds,coalesce(sum(input_bytes),0) bytes FROM jobs").fetchone()),
          host=host_snapshot,telemetry_error=telemetry_error,now=time.time(),versions=obs.package_versions(),
          models=MODELS,catalog=installed_models(os.getenv('TTS_MODELS',str(ROOT/'models'))))

def admin_job(r):
    data = {k:r[k] for k in ('id','title','status','outcome','created','started','finished','expires','heartbeat',
        'client_tag','network_tag','ui_language','priority','stage','attempts','worker','model','device','compute',
        'duration','elapsed','cpu_seconds','peak_rss_mb','peak_vram_mb','input_bytes','language','error','model_revision','runtime','engine','progress','result_state','purged_at','purge_attempts','purge_retry_at','purge_error')}
    data['metrics'] = json.loads(r['metrics_json']) if r['metrics_json'] else None
    return data

@app.get('/api/admin/history',dependencies=[Depends(admin)])
def history(period: str='day'):
    if period not in obs.PERIODS: raise HTTPException(400,'Período inválido.')
    with db() as c: return obs.history(c,period)

@app.get('/api/admin/jobs',dependencies=[Depends(admin)])
def admin_jobs(status: str='',search: str='',page: int=1,limit: int=25):
    if page<1 or page>100000 or limit not in (25,50,100) or len(search)>120:
        raise HTTPException(400,'Filtro inválido.')
    if status and status not in ('queued','running','done','failed','expired','cancelled','uploading'):
        raise HTTPException(400,'Estado inválido.')
    filters=['1=1'];params=[]
    if status: filters.append('status=?');params.append(status)
    if search:
        filters.append("(id LIKE ? ESCAPE '\\' OR client_tag LIKE ? ESCAPE '\\' OR network_tag LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR worker LIKE ? ESCAPE '\\')")
        needle='%'+search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        params.extend([needle]*5)
    where=' AND '.join(filters)
    with db() as c:
        count=c.execute('SELECT count(*) FROM jobs WHERE '+where,params).fetchone()[0]
        rows=c.execute('SELECT * FROM jobs WHERE '+where+' ORDER BY created DESC,id LIMIT ? OFFSET ?',params+[limit,(page-1)*limit]).fetchall()
    return dict(total=count,page=page,limit=limit,jobs=[admin_job(r) for r in rows])

@app.get('/api/admin/clients',dependencies=[Depends(admin)])
def clients(period: str='day'):
    if period not in obs.PERIODS: raise HTTPException(400,'Período inválido.')
    start=time.time()-obs.PERIODS[period][0]
    with db() as c:
        rows=c.execute("""SELECT client_tag,count(*) jobs,min(created) first_seen,max(created) last_seen,
        sum(CASE WHEN status IN ('queued','running') THEN 1 ELSE 0 END) active,
        coalesce(sum(duration),0) audio_seconds,coalesce(sum(elapsed),0) processing_seconds,
        coalesce(sum(input_bytes),0) bytes,count(DISTINCT network_tag) networks
        FROM jobs WHERE created>=? AND client_tag IS NOT NULL GROUP BY client_tag ORDER BY jobs DESC LIMIT 100""",(start,)).fetchall()
        networks=c.execute("""SELECT network_tag,count(*) jobs,count(DISTINCT client_tag) sessions,coalesce(sum(duration),0) audio_seconds
          FROM jobs WHERE created>=? AND network_tag IS NOT NULL GROUP BY network_tag ORDER BY jobs DESC LIMIT 50""",(start,)).fetchall()
    return dict(clients=[dict(r) for r in rows],networks=[dict(r) for r in networks],start=start)

@app.get('/api/admin/jobs/{jid}',dependencies=[Depends(admin)])
def job_detail(jid: str):
    with db() as c:
        r=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not r: raise HTTPException(404,'Trabalho não encontrado.')
        events=[dict(e)|{'data':json.loads(e['data'])} for e in c.execute('SELECT * FROM events WHERE job=? ORDER BY at,id',(jid,))]
    return dict(job=admin_job(r),events=events)

class Priority(BaseModel):
    priority: int = Field(ge=0,le=9)

@app.patch('/api/admin/jobs/{jid}/priority',dependencies=[Depends(admin)])
def priority(jid: str,value: Priority):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        r=c.execute('SELECT status,priority FROM jobs WHERE id=?',(jid,)).fetchone()
        if not r: raise HTTPException(404,'Trabalho não encontrado.')
        if r['status']!='queued': raise HTTPException(409,'Só é possível priorizar trabalhos na fila.')
        c.execute('UPDATE jobs SET priority=? WHERE id=?',(value.priority,jid))
        obs.event(c,jid,'priority_changed','admin',{'from':r['priority'],'to':value.priority})
    return {'ok':True}

@app.put('/api/admin/settings', dependencies=[Depends(admin)])
def settings(s: Settings):
    if s.engine=='whisper':
        valid = s.model in MODELS and (s.device,s.compute) in (
            ('cpu','int8'),('cpu','float32'),('cuda','float16'),('cuda','int8_float16'))
    elif s.engine=='qwen3':
        from server.qwen_engine import RECIPES
        valid = s.model in RECIPES and s.device=='cuda' and s.compute in ('float16','bfloat16')
    else:
        valid = False
    if not valid or not s.language_hint.replace('-','').isalpha():
        raise HTTPException(400,'Combinação de motor, modelo, idioma, dispositivo ou precisão inválida.')
    with db() as c:
        c.execute('UPDATE settings SET value=? WHERE id=1', (s.model_dump_json(),))
        obs.event(c,None,'settings_changed','admin',s.model_dump())
    return s

class Claim(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    info: dict = Field(default_factory=dict)

@app.post('/api/worker/claim', dependencies=[Depends(worker)])
def claim(w: Claim):
    now = time.time()
    info=json.dumps(w.info)
    if len(info)>8000: raise HTTPException(413,'Metadados do executor excedem o limite.')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT OR REPLACE INTO workers VALUES (?,?,?)', (w.id,now,info))
        cfg = json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
        if cfg['paused'] or cfg['worker'] != w.id:
            return {'job':None}
        if cfg.get('engine','whisper')=='qwen3' and cfg['model'] not in w.info.get('qwen_models',[]):
            return {'job':None}
        if c.execute("SELECT 1 FROM jobs WHERE status='running' AND worker=?",(w.id,)).fetchone():
            return {'job':None}
        r = c.execute("SELECT * FROM jobs WHERE status='queued' AND expires>? ORDER BY priority DESC,created,id LIMIT 1", (now,)).fetchone()
        if not r: return {'job':None}
        lease = secrets.token_hex(32)
        c.execute("UPDATE jobs SET status='running',started=?,worker=?,model=?,device=?,compute=?,engine=?,lease=?,heartbeat=?,attempts=attempts+1,error=NULL WHERE id=?", (now,w.id,cfg['model'],cfg['device'],cfg['compute'],cfg.get('engine','whisper'),lease,now,r['id']))
        c.execute("UPDATE jobs SET stage='preparing' WHERE id=?",(r['id'],))
        obs.event(c,r['id'],'started',w.id,dict(model=cfg['model'],device=cfg['device'],compute=cfg['compute']))
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
    stage: str = Field(default='preparing',pattern='^(preparing|downloading|loading|transcribing|finishing)$')
    runtime: dict = Field(default_factory=dict)

@app.post('/api/worker/{jid}/heartbeat', dependencies=[Depends(worker)])
def heartbeat(jid: str, h: Heartbeat):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        r = leased(c,jid,h.lease)
        now = time.time()
        changed = c.execute("""UPDATE jobs SET heartbeat=?,progress=?,stage=?
                               WHERE id=? AND lease=? AND status='running' AND expires>?""",
                            (now,h.progress,h.stage,jid,h.lease,now))
        if changed.rowcount != 1:
            raise HTTPException(409,'Lease expirado.')
        if r['stage'] != h.stage:
            obs.event(c,jid,'stage',r['worker'],{'stage':h.stage})
        c.execute('UPDATE workers SET seen=? WHERE id=?',(now,r['worker']))
    return {'ok':True}

class Segment(BaseModel):
    start: float = Field(ge=0,le=7200,allow_inf_nan=False)
    end: float = Field(ge=0,le=7200,allow_inf_nan=False)
    text: str = Field(max_length=10000)

class Result(BaseModel):
    lease: str
    segments: list[Segment] = Field(default_factory=list,max_length=30000)
    language: str = Field(default='',max_length=20)
    duration: float = Field(default=0,ge=0,le=7200,allow_inf_nan=False)
    elapsed: float = Field(default=0,ge=0,allow_inf_nan=False)
    cpu_seconds: float = Field(default=0,ge=0,allow_inf_nan=False)
    peak_rss_mb: float = Field(default=0,ge=0,allow_inf_nan=False)
    peak_vram_mb: float = Field(default=0,ge=0,allow_inf_nan=False)
    input_bytes: int = Field(default=0,ge=0)
    error: str | None = Field(default=None,max_length=500)
    runtime: dict = Field(default_factory=dict)
    model_revision: str | None = Field(default=None,max_length=80)
    title: str | None = Field(default=None,max_length=180)
    result_state: str = Field(default='unknown',pattern='^(unknown|complete|silence|partial)$')
    metrics: dict | None = None
    text: str | None = Field(default=None,max_length=500000)
    timing: str = Field(default='segments',pattern='^(segments|none)$')

    @model_validator(mode='after')
    def chronology(self):
        if self.metrics is not None:
            validate_metrics(self.metrics)
        if self.timing == 'none' and self.segments:
            raise ValueError('Untimed text cannot contain timed segments.')
        if self.error and self.segments:
            raise ValueError('Erro não pode conter segmentos publicados.')
        previous = -1.0
        for segment in self.segments:
            if segment.end < segment.start:
                raise ValueError('Segmento com intervalo invertido.')
            if segment.start + 1e-6 < previous:
                raise ValueError('Segmentos fora da ordem cronológica.')
            if self.duration == 0 or segment.end > self.duration + 0.5:
                raise ValueError('Segmento além da duração declarada.')
            previous = segment.start  # legitimate overlapping speech is preserved
        if self.result_state == 'silence' and self.segments:
            raise ValueError('Silêncio não pode conter segmentos.')
        if self.result_state in ('complete','partial') and not self.segments and not (self.text or '').strip() and not self.error:
            raise ValueError('Resultado vazio exige estado silêncio ou desconhecido.')
        return self

def timestamp(n):
    ms = int(n*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

@app.post('/api/worker/{jid}/result', dependencies=[Depends(worker)])
def result(jid: str, result: Result):
    canonical = json.dumps(result.model_dump(),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    receipt = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    lease_hash = hashlib.sha256(result.lease.encode()).hexdigest()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        previous = c.execute('SELECT status,expires,receipt_hash,receipt_lease FROM jobs WHERE id=?',(jid,)).fetchone()
        if previous and previous['expires']>time.time() and previous['status'] in ('done','failed'):
            if previous['receipt_hash']==receipt and previous['receipt_lease']==lease_hash:
                return {'ok':True,'receipt':receipt,'duplicate':True}
            raise HTTPException(409,'Tentativa concluída com resultado diferente.')
        r = leased(c,jid,result.lease)
        if not result.error:
            folder = BLOB / jid
            folder.mkdir(exist_ok=True)
            text = result.text.strip() if result.text is not None else '\n'.join(s.text.strip() for s in result.segments)
            exports = {
                'txt': text,
                'md': '# Transcrição\n\n' + text,
                **({'srt': '\n\n'.join(f'{i}\n{timestamp(s.start)} --> {timestamp(s.end)}\n{s.text.strip()}' for i,s in enumerate(result.segments,1))} if result.timing=='segments' else {}),
                'json': json.dumps(dict(schema_version=1,result_state=result.result_state,timing=result.timing,language=result.language,duration=result.duration,
                                        model=r['model'],text=text if result.timing=='none' else None,segments=[s.model_dump() for s in result.segments]),ensure_ascii=False)
            }
            for fmt, content in exports.items():
                tmp = folder / ('transcript.' + fmt + '.tmp')
                tmp.write_text(content,encoding='utf-8')
                tmp.replace(folder / ('transcript.' + fmt))
        state = 'failed' if result.error else 'done'
        outcome = 'failed' if result.error else ('partial' if result.result_state=='partial' else 'done')
        c.execute("""UPDATE jobs SET status=?,finished=?,duration=?,elapsed=?,cpu_seconds=?,peak_rss_mb=?,
                   peak_vram_mb=?,language=?,error=?,progress=?,lease=NULL,input_bytes=max(input_bytes,?),
                   receipt_hash=?,receipt_lease=?,result_state=? WHERE id=? AND lease=? AND status='running'""",
                  (state,time.time(),result.duration,result.elapsed,result.cpu_seconds,result.peak_rss_mb,
                   result.peak_vram_mb,result.language,result.error,0 if result.error else 100,result.input_bytes,
                   receipt,lease_hash,result.result_state,jid,result.lease))
        runtime=json.dumps(result.runtime)
        if len(runtime)>8000: runtime=json.dumps({'metadata_error':'oversized_runtime'})
        c.execute('UPDATE jobs SET outcome=?,stage=?,runtime=?,model_revision=?,metrics_json=? WHERE id=?',
                  (outcome,state,runtime,result.model_revision,json.dumps(result.metrics) if result.metrics is not None else None,jid))
        if result.title and r['source']!='upload': c.execute('UPDATE jobs SET title=? WHERE id=?',(result.title,jid))
        obs.event(c,jid,outcome,r['worker'],{'elapsed':result.elapsed})
    return {'ok':True,'receipt':receipt,'duplicate':False}

app.mount('/', StaticFiles(directory=ROOT/'web',html=True),name='web')
)
    metrics: dict | None = None
    text: str | None = Field(default=None,max_length=500000)
    timing: str = Field(default='segments',pattern='^(segments|none)

    @model_validator(mode='after')
    def chronology(self):
        if self.error and self.segments:
            raise ValueError('Erro não pode conter segmentos publicados.')
        previous = -1.0
        for segment in self.segments:
            if segment.end < segment.start:
                raise ValueError('Segmento com intervalo invertido.')
            if segment.start + 1e-6 < previous:
                raise ValueError('Segmentos fora da ordem cronológica.')
            if self.duration == 0 or segment.end > self.duration + 0.5:
                raise ValueError('Segmento além da duração declarada.')
            previous = segment.start  # legitimate overlapping speech is preserved
        if self.result_state == 'silence' and self.segments:
            raise ValueError('Silêncio não pode conter segmentos.')
        if self.result_state in ('complete','partial') and not self.segments and not self.error:
            raise ValueError('Resultado vazio exige estado silêncio ou desconhecido.')
        return self

def timestamp(n):
    ms = int(n*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

@app.post('/api/worker/{jid}/result', dependencies=[Depends(worker)])
def result(jid: str, result: Result):
    canonical = json.dumps(result.model_dump(),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    receipt = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    lease_hash = hashlib.sha256(result.lease.encode()).hexdigest()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        previous = c.execute('SELECT status,expires,receipt_hash,receipt_lease FROM jobs WHERE id=?',(jid,)).fetchone()
        if previous and previous['expires']>time.time() and previous['status'] in ('done','failed'):
            if previous['receipt_hash']==receipt and previous['receipt_lease']==lease_hash:
                return {'ok':True,'receipt':receipt,'duplicate':True}
            raise HTTPException(409,'Tentativa concluída com resultado diferente.')
        r = leased(c,jid,result.lease)
        if not result.error:
            folder = BLOB / jid
            folder.mkdir(exist_ok=True)
            text = '\n'.join(s.text.strip() for s in result.segments)
            exports = {
                'txt': text,
                'md': '# Transcrição\n\n' + text,
                'srt': '\n\n'.join(f'{i}\n{timestamp(s.start)} --> {timestamp(s.end)}\n{s.text.strip()}' for i,s in enumerate(result.segments,1)),
                'json': json.dumps(dict(schema_version=1,result_state=result.result_state,language=result.language,duration=result.duration,
                                        model=r['model'],segments=[s.model_dump() for s in result.segments]),ensure_ascii=False)
            }
            for fmt, content in exports.items():
                tmp = folder / ('transcript.' + fmt + '.tmp')
                tmp.write_text(content,encoding='utf-8')
                tmp.replace(folder / ('transcript.' + fmt))
        state = 'failed' if result.error else 'done'
        outcome = 'failed' if result.error else ('partial' if result.result_state=='partial' else 'done')
        c.execute("""UPDATE jobs SET status=?,finished=?,duration=?,elapsed=?,cpu_seconds=?,peak_rss_mb=?,
                   peak_vram_mb=?,language=?,error=?,progress=?,lease=NULL,input_bytes=max(input_bytes,?),
                   receipt_hash=?,receipt_lease=?,result_state=? WHERE id=? AND lease=? AND status='running'""",
                  (state,time.time(),result.duration,result.elapsed,result.cpu_seconds,result.peak_rss_mb,
                   result.peak_vram_mb,result.language,result.error,0 if result.error else 100,result.input_bytes,
                   receipt,lease_hash,result.result_state,jid,result.lease))
        runtime=json.dumps(result.runtime)
        if len(runtime)>8000: runtime=json.dumps({'metadata_error':'oversized_runtime'})
        c.execute('UPDATE jobs SET outcome=?,stage=?,runtime=?,model_revision=? WHERE id=?',(outcome,state,runtime,result.model_revision,jid))
        if result.title and r['source']!='upload': c.execute('UPDATE jobs SET title=? WHERE id=?',(result.title,jid))
        obs.event(c,jid,outcome,r['worker'],{'elapsed':result.elapsed})
    return {'ok':True,'receipt':receipt,'duplicate':False}

app.mount('/', StaticFiles(directory=ROOT/'web',html=True),name='web')
)

    @model_validator(mode='after')
    def chronology(self):
        if self.error and self.segments:
            raise ValueError('Erro não pode conter segmentos publicados.')
        previous = -1.0
        for segment in self.segments:
            if segment.end < segment.start:
                raise ValueError('Segmento com intervalo invertido.')
            if segment.start + 1e-6 < previous:
                raise ValueError('Segmentos fora da ordem cronológica.')
            if self.duration == 0 or segment.end > self.duration + 0.5:
                raise ValueError('Segmento além da duração declarada.')
            previous = segment.start  # legitimate overlapping speech is preserved
        if self.result_state == 'silence' and self.segments:
            raise ValueError('Silêncio não pode conter segmentos.')
        if self.result_state in ('complete','partial') and not self.segments and not self.error:
            raise ValueError('Resultado vazio exige estado silêncio ou desconhecido.')
        return self

def timestamp(n):
    ms = int(n*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

@app.post('/api/worker/{jid}/result', dependencies=[Depends(worker)])
def result(jid: str, result: Result):
    canonical = json.dumps(result.model_dump(),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    receipt = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    lease_hash = hashlib.sha256(result.lease.encode()).hexdigest()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        previous = c.execute('SELECT status,expires,receipt_hash,receipt_lease FROM jobs WHERE id=?',(jid,)).fetchone()
        if previous and previous['expires']>time.time() and previous['status'] in ('done','failed'):
            if previous['receipt_hash']==receipt and previous['receipt_lease']==lease_hash:
                return {'ok':True,'receipt':receipt,'duplicate':True}
            raise HTTPException(409,'Tentativa concluída com resultado diferente.')
        r = leased(c,jid,result.lease)
        if not result.error:
            folder = BLOB / jid
            folder.mkdir(exist_ok=True)
            text = '\n'.join(s.text.strip() for s in result.segments)
            exports = {
                'txt': text,
                'md': '# Transcrição\n\n' + text,
                'srt': '\n\n'.join(f'{i}\n{timestamp(s.start)} --> {timestamp(s.end)}\n{s.text.strip()}' for i,s in enumerate(result.segments,1)),
                'json': json.dumps(dict(schema_version=1,result_state=result.result_state,language=result.language,duration=result.duration,
                                        model=r['model'],segments=[s.model_dump() for s in result.segments]),ensure_ascii=False)
            }
            for fmt, content in exports.items():
                tmp = folder / ('transcript.' + fmt + '.tmp')
                tmp.write_text(content,encoding='utf-8')
                tmp.replace(folder / ('transcript.' + fmt))
        state = 'failed' if result.error else 'done'
        outcome = 'failed' if result.error else ('partial' if result.result_state=='partial' else 'done')
        c.execute("""UPDATE jobs SET status=?,finished=?,duration=?,elapsed=?,cpu_seconds=?,peak_rss_mb=?,
                   peak_vram_mb=?,language=?,error=?,progress=?,lease=NULL,input_bytes=max(input_bytes,?),
                   receipt_hash=?,receipt_lease=?,result_state=? WHERE id=? AND lease=? AND status='running'""",
                  (state,time.time(),result.duration,result.elapsed,result.cpu_seconds,result.peak_rss_mb,
                   result.peak_vram_mb,result.language,result.error,0 if result.error else 100,result.input_bytes,
                   receipt,lease_hash,result.result_state,jid,result.lease))
        runtime=json.dumps(result.runtime)
        if len(runtime)>8000: runtime=json.dumps({'metadata_error':'oversized_runtime'})
        c.execute('UPDATE jobs SET outcome=?,stage=?,runtime=?,model_revision=? WHERE id=?',(outcome,state,runtime,result.model_revision,jid))
        if result.title and r['source']!='upload': c.execute('UPDATE jobs SET title=? WHERE id=?',(result.title,jid))
        obs.event(c,jid,outcome,r['worker'],{'elapsed':result.elapsed})
    return {'ok':True,'receipt':receipt,'duplicate':False}

app.mount('/', StaticFiles(directory=ROOT/'web',html=True),name='web')
