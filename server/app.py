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

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from server.catalog import MODEL_CATALOG, installed_models
from server import observability as obs
from server.result_contract import Result, Segment, result_receipt
from server.config import getenv
from server.ssh_targets import ssh_targets
from server import admin_auth
from server import hosting
from server.video_links import video_link, VideoLinkError

load_dotenv()
ROOT = Path(__file__).resolve().parents[1]
DATA = Path(getenv('STT_DATA', str(ROOT))).resolve()
BLOB = DATA / 'blob'
DB = DATA / 'db' / 'stt.sqlite3'
MAX_BYTES = 90 * 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024**2
TTL = 86400
OPERATIONAL_RETENTION = 30 * TTL
AGGREGATE_RETENTION = 5 * 365 * TTL
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
        CREATE INDEX IF NOT EXISTS jobs_expires ON jobs(expires);
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, seen REAL, info TEXT);
        CREATE TABLE IF NOT EXISTS worker_controls (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS admission (client TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS upload_slots (id TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL);
        ''')
        columns={r[1] for r in c.execute('PRAGMA table_info(jobs)')}
        for name,definition in {
            'gpu_name':'TEXT','client_tag':'TEXT','network_tag':'TEXT','ui_language':'TEXT',
            'priority':'INTEGER DEFAULT 0','outcome':'TEXT','runtime':'TEXT',
            'model_revision':'TEXT','stage':"TEXT DEFAULT 'queued'",
            'purged_at':'REAL','purge_retry_at':'REAL DEFAULT 0','purge_attempts':'INTEGER DEFAULT 0',
            'purge_error':'TEXT','receipt_hash':'TEXT','receipt_lease':'TEXT','result_state':'TEXT','metrics_json':'TEXT','engine':'TEXT','timing':"TEXT DEFAULT 'segments'",'artifact_layout':'TEXT'}.items():
            if name not in columns: c.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
        c.execute("UPDATE jobs SET outcome=status WHERE outcome IS NULL AND status IN ('done','failed','cancelled')")
        c.execute("UPDATE jobs SET stage=status WHERE stage='queued' AND status IN ('done','failed','expired','cancelled')")
        # Backfill an anonymous administrative label; this is not an access credential.
        for row in c.execute("SELECT id,owner FROM jobs WHERE client_tag IS NULL AND owner!=''").fetchall():
            c.execute('UPDATE jobs SET client_tag=? WHERE id=?',(client_tag(row['owner']),row['id']))
        obs.install(c)
        admin_auth.install_schema(c)
        c.execute("""CREATE TABLE IF NOT EXISTS model_preloads (
            id TEXT PRIMARY KEY, worker TEXT NOT NULL, model TEXT NOT NULL,
            status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
            revision TEXT, error TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS model_preloads_worker ON model_preloads(worker,status,updated)")
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
        admin_auth.cleanup(c, now)
        c.execute("UPDATE model_preloads SET status='pending',updated=?,error='REQUEUED_AFTER_TIMEOUT' WHERE status='running' AND updated<?",(now,now-600))
        c.execute("DELETE FROM model_preloads WHERE status IN ('ready','failed') AND updated<?",(now-7*TTL,))
        c.execute("""UPDATE jobs SET status='expired',stage='expired',owner='',
                  title=NULL,source=NULL,error=NULL,lease=NULL,language=NULL
                  WHERE expires<=? AND status!='expired'""", (now,))
        rows = c.execute("""SELECT id FROM jobs WHERE expires<=? AND purged_at IS NULL
                            AND coalesce(purge_retry_at,0)<=?""", (now,now)).fetchall()
        for row in rows:
            _purge_job(c,row['id'],now)
        for row in c.execute("""SELECT id,attempts,started FROM jobs WHERE status='running'
                AND (coalesce(heartbeat,started,created)<? OR coalesce(started,created)<?)
                AND expires>?""",(now-180,now-7200,now)).fetchall():
            failed=row['attempts']>=3
            c.execute("UPDATE jobs SET status=?,outcome=?,stage=?,error=?,lease=NULL,progress=0 WHERE id=?",('failed' if failed else 'queued','failed' if failed else None,'failed' if failed else 'queued','Executor desconectado; limite de tentativas atingido.' if failed else None,row['id']))
            c.execute('UPDATE jobs SET finished=? WHERE id=?',(now if failed else None,row['id']))
            reason='execution_deadline' if row['started'] and row['started']<now-7200 else 'heartbeat_timeout'
            obs.event(c,row['id'],'failed' if failed else 'requeued',data={'reason':reason})
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
        # Privacy budget: anonymous operational identifiers and granular events
        # have a finite 30-day lifetime; aggregate counters stay available.
        boundary = now - OPERATIONAL_RETENTION
        c.execute('DELETE FROM events WHERE at<?',(boundary,))
        c.execute("""UPDATE jobs SET client_tag=NULL,network_tag=NULL,ui_language=NULL
                     WHERE expires<? AND
                     (client_tag IS NOT NULL OR network_tag IS NOT NULL OR ui_language IS NOT NULL)""",
                  (boundary,))
        c.execute('DELETE FROM workers WHERE seen<?',(boundary,))
        # Historical trends intentionally cover at most five years. Retain a
        # job purge receipt when physical deletion has not succeeded yet.
        oldest = now - AGGREGATE_RETENTION
        c.execute('DELETE FROM jobs WHERE expires<? AND purged_at IS NOT NULL',(oldest,))
        c.execute('DELETE FROM telemetry WHERE bucket<?',(oldest,))

stop = threading.Event()
host_snapshot={}
telemetry_error=None
def collect():
    global host_snapshot,telemetry_error
    import psutil
    gpu=obs.gpu_info()
    ram=psutil.virtual_memory()
    disk=shutil.disk_usage(BLOB)
    try:
        cpu=psutil.cpu_percent(interval=.1)
    except (OSError,psutil.Error):
        cpu=None
    host_snapshot=dict(at=time.time(),cpu_percent=cpu,ram_percent=ram.percent,
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

def secure_api_response(response, path):
    """Apply the same privacy headers to normal and early-rejected responses."""
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if path.startswith('/api/') or path in ('/admin.html', '/admin-approve.html'):
        response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'")
    response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


@app.middleware('http')
async def guard(request: Request, call_next):
    def denied(detail, status):
        return secure_api_response(JSONResponse({'detail':detail}, status_code=status),
                                   request.url.path)
    length = request.headers.get('content-length')
    if length and (not length.isdecimal() or int(length)>body_limit(request.url.path)):
        return denied('Corpo da requisição excede o limite.', 413)
    if getenv('STT_REQUIRE_EDGE') == '1':
        key = getenv('STT_EDGE_KEY', '')
        if not key or not hmac.compare_digest(request.headers.get('x-edge-key', ''), key):
            return denied('Origem não autorizada.', 403)
    if request.url.path in ('/api/jobs/upload','/api/jobs/youtube','/api/jobs/link') and request.method=='POST':
        token=request.headers.get('x-session-key','')
        if len(token)!=64 or any(ch not in '0123456789abcdef' for ch in token):
            return denied('Sessão inválida.', 401)
    slot = None
    try:
        slot = preflight(request)
    except HTTPException as exc:
        return denied(exc.detail, exc.status_code)
    request.state.upload_slot = slot
    try:
        response = await call_next(request)
    finally:
        if slot:
            with db() as c:
                c.execute('DELETE FROM upload_slots WHERE id=?',(slot,))
    return secure_api_response(response, request.url.path)

def secret(request, env, header):
    expected = getenv(env, '')
    if not expected or not hmac.compare_digest(request.headers.get(header, ''), expected):
        raise HTTPException(401, 'Chave inválida.')

def admin(request: Request):
    admin_auth.authorize(request, db, getenv)

def worker(request: Request):
    """S7 has an independently rotatable key scoped to its own jobs."""
    presented = request.headers.get('x-worker-key','')
    scoped = getenv('STT_S7_WORKER_KEY','')
    shared = getenv('STT_WORKER_KEY','')
    if scoped and hmac.compare_digest(presented,scoped):
        request.state.worker_scope = 's7-edge'
    elif shared and hmac.compare_digest(presented,shared):
        request.state.worker_scope = None
    else:
        raise HTTPException(401,'Chave inválida.')

def require_s7_scope(request: Request, identity: str):
    scope = getattr(request.state,'worker_scope',None)
    if scope is not None and scope != identity:
        raise HTTPException(403,'Chave restrita a outro executor.')

def owner(request: Request):
    token = request.headers.get('x-session-key', '')
    if len(token) != 64 or any(x not in '0123456789abcdef' for x in token):
        raise HTTPException(401, 'Sessão inválida.')
    return hashlib.sha256(token.encode()).hexdigest()

def preflight(request: Request):
    """Admission runs in HTTP middleware, BEFORE Starlette reads multipart bytes."""
    if request.method != 'POST' or request.url.path not in ('/api/jobs/upload','/api/jobs/youtube','/api/jobs/link'):
        return None
    session = request.headers['x-session-key']
    own = hashlib.sha256(session.encode()).hexdigest()
    ip = request.headers.get('x-stt-client') if getenv('STT_REQUIRE_EDGE')=='1' else (request.client.host if request.client else None)
    key = hashlib.sha256((getenv('STT_EDGE_KEY','local') + (ip or 'unknown')).encode()).hexdigest()
    now = time.time()
    is_upload = request.url.path.endswith('/upload')
    slot = secrets.token_hex(16) if is_upload else None
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        capacity = hosting.capabilities(c)
        if not is_upload and not capacity['youtube']:
            raise HTTPException(503, 'Modo limitado: envie áudio WAV, MP3, OGG ou M4A de até 5 minutos.')
        length = request.headers.get('content-length', '')
        if length.isdecimal() and int(length) > capacity['max_mb']*1024**2+1024**2:
            raise HTTPException(413, 'Arquivo excede o limite disponível no momento.')
        hosting.storage_guard(BLOB, reserve=capacity['max_mb']*1024**2)
        c.execute('DELETE FROM upload_slots WHERE created<?',(now-7200,))
        if c.execute('SELECT count(*) FROM admission WHERE client=? AND created>?',(key,now-3600)).fetchone()[0] >= 20:
            raise HTTPException(429,'Limite de envios desta rede atingido. Tente mais tarde.')
        active = c.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','uploading','running')").fetchone()[0]
        inflight = c.execute('SELECT count(*) FROM upload_slots').fetchone()[0]
        if active + inflight >= 20:
            raise HTTPException(429,'Fila cheia. Tente novamente mais tarde.')
        previous_jobs = c.execute('SELECT count(*) FROM jobs WHERE owner=? AND created>?',(own,now-3600)).fetchone()[0]
        reserved = c.execute('SELECT count(*) FROM upload_slots WHERE owner=? AND created>?',(own,now-3600)).fetchone()[0]
        if previous_jobs + reserved >= 10:
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
    return hmac.new(getenv('STT_ADMIN_KEY','local').encode(),own.encode(),'sha256').hexdigest()[:16]

def public(r):
    result={k:r[k] for k in ('id','title','status','created','expires','started','finished','error','duration','elapsed','language','progress','stage','timing','result_state')}
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
    ip=request.headers.get('x-stt-client') if request and getenv('STT_REQUIRE_EDGE')=='1' else None
    network=hmac.new(getenv('STT_EDGE_KEY','local').encode(),ip.encode(),'sha256').hexdigest()[:16] if ip else None
    lang=request.headers.get('x-ui-language','en')[:12] if request else 'en'
    c.execute('UPDATE jobs SET client_tag=?,network_tag=?,ui_language=? WHERE id=?',(client_tag(own),network,lang,jid))
    obs.event(c,jid,'submitted',actor='session:'+client_tag(own),data={'source':'upload' if source=='upload' else 'link'})
    return jid

def body_limit(path):
    if path=='/api/jobs/upload':return MAX_BYTES+1024**2
    if path.startswith('/api/worker/') and path.endswith('/result'):return MAX_RESULT_BYTES
    return 32768


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        size = 0
        exceeded = False
        responded = False
        started = False
        depth = 0
        quoted = False
        escaped = False
        maximum = body_limit(scope.get('path',''))
        if getenv('STT_LIGHT_HOST','0')=='1' and scope.get('path')=='/api/jobs/upload':
            with db() as connection:
                maximum = hosting.capabilities(connection)['max_mb']*1024**2+1024**2

        async def limited():
            nonlocal size, exceeded, depth, quoted, escaped
            if exceeded:
                return {'type':'http.request', 'body':b'', 'more_body':False}
            message = await receive()
            if message['type'] != 'http.request':
                return message
            size += len(message.get('body', b''))
            if scope.get('path','')!='/api/jobs/upload':
                # Reject pathological nesting before Python's JSON decoder.
                # Track quotes/escapes across chunks; braces in text are data.
                for byte in message.get('body',b''):
                    if quoted:
                        if escaped:escaped=False
                        elif byte==92:escaped=True
                        elif byte==34:quoted=False
                    elif byte==34:quoted=True
                    elif byte in (91,123):
                        depth+=1
                        if depth>32:exceeded=True;break
                    elif byte in (93,125):depth-=1
            if exceeded or size > maximum:
                # The multipart parser may turn exceptions raised by receive()
                # into HTTP 400. Send an explicit 413 instead, while ensuring
                # no further bytes are passed to the application.
                exceeded = True
                return {'type':'http.request', 'body':b'', 'more_body':False}
            return message

        async def reject():
            response = secure_api_response(
                JSONResponse({'detail':'Arquivo excede o limite disponível no momento.'}, status_code=413),
                scope.get('path',''))
            await response(scope, receive, send)

        async def limited_send(message):
            nonlocal responded, started
            if exceeded:
                if not responded and not started:
                    responded = True
                    await reject()
                return
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        try:
            await self.app(scope, limited, limited_send)
        except Exception:
            if exceeded and not started and not responded:
                await reject()
                return
            raise

app.add_middleware(BodyLimit)

@app.get('/api/health')
def health():
    with db() as c:
        queue = c.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        cfg = json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
        recent = c.execute("SELECT count(*) FROM workers WHERE seen>? AND id=? AND coalesce(json_extract(info,'$.ready'),1)!=0", (time.time()-90,cfg['worker'])).fetchone()[0]
        if not worker_enabled(c,cfg['worker']):recent=0
        waiting=c.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0]
        running=c.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]
        capacity=hosting.capabilities(c)
        if not recent and worker_enabled(c,'s7-edge') and getenv('STT_S7_ENABLED','0')=='1' and getenv('STT_S7_SIDE_WORKER','0')=='1':
            recent=c.execute("SELECT count(*) FROM workers WHERE id='s7-edge' AND seen>? AND coalesce(json_extract(info,'$.ready'),1)!=0",(time.time()-90,)).fetchone()[0]
    pressure='free' if queue==0 else 'short' if queue<=3 else 'medium' if queue<=10 else 'high'
    return dict(online=bool(recent), queue=queue,waiting=waiting,running=running,pressure=pressure,paused=cfg['paused'],retention_hours=24,**capacity)

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
        capacity = hosting.capabilities(c)
        if capacity['formats'] and suffix.lstrip('.') not in capacity['formats']:
            raise HTTPException(400, 'Modo limitado: envie WAV, MP3, OGG ou M4A de até 5 minutos e 8 MiB.')
        jid = new_job(c, own, Path(file.filename).name[:180], 'upload',request)
    folder = BLOB / jid
    created = False
    size = 0
    try:
        folder.mkdir()
        created = True
        with (folder / 'input.media').open('wb') as f:
            while chunk := await file.read(1024*1024):
                size += len(chunk)
                if size > capacity['max_mb']*1024**2:
                    raise HTTPException(413, 'Arquivo excede o limite disponível no momento.')
                hosting.storage_guard(BLOB, reserve=len(chunk))
                f.write(chunk)
        if not size:
            raise HTTPException(400, 'Arquivo vazio.')
        duration = None
        if getenv('STT_LIGHT_HOST','0')=='1' and suffix.lstrip('.') in hosting.SMALL_FORMATS and size<=8*1024**2:
            duration = hosting.duration_probe(folder/'input.media', suffix.lstrip('.'))
            if capacity['limited'] and duration > capacity['max_seconds']:
                raise HTTPException(413, 'Modo limitado: áudio de até 5 minutos.')
        with db() as c:
            c.execute("UPDATE jobs SET status='queued',input_bytes=?,duration=? WHERE id=?", (size,duration,jid))
    except BaseException:
        # The directory is ours only after mkdir succeeds. Never remove a
        # pre-existing directory when disk creation failed.
        if created:
            shutil.rmtree(folder, ignore_errors=True)
        with db() as c:
            c.execute('DELETE FROM jobs WHERE id=?', (jid,))
        raise
    return {'id':jid}

class Link(BaseModel):
    url: str = Field(max_length=2048)

def youtube_url(value):
    # Compatibility helper for callers that explicitly require YouTube.
    try:
        link=video_link(value)
        if link.platform!='YouTube':raise VideoLinkError('YouTube required')
        return link.url
    except VideoLinkError:
        raise HTTPException(400,'Link de vídeo do YouTube inválido.') from None

@app.post('/api/jobs/link', status_code=201)
@app.post('/api/jobs/youtube', status_code=201,include_in_schema=False)
def submit_link(link: Link, request: Request, own=Depends(owner)):
    try:parsed=video_link(link.url)
    except VideoLinkError:
        raise HTTPException(400,'Use um link de vídeo do YouTube, Instagram, TikTok, Facebook ou X.') from None
    url=parsed.url
    folder = None
    created = False
    try:
        with db() as c:
            jid = new_job(c, own, parsed.platform, url,request)
            folder = BLOB / jid
            # The job must not be published in SQLite without durable input
            # workspace; failure here rolls back the queue insertion.
            folder.mkdir()
            created = True
            c.execute("UPDATE jobs SET status='queued' WHERE id=?", (jid,))
    except BaseException:
        if created:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    return {'id':jid}

def artifact_path(row, fmt):
    """Committed generation only; old rows retain the pre-upgrade flat layout."""
    if row['artifact_layout'] == 'generations-v1':
        digest = row['receipt_hash']
        if not digest or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise HTTPException(503, 'Manifesto do resultado indisponível.')
        return BLOB / row['id'] / 'results' / digest / ('transcript.' + fmt)
    return BLOB / row['id'] / ('transcript.' + fmt)


def _fsync_directory(path):
    """POSIX directory durability; on Windows use per-file fsync and atomic rename."""
    if os.name == 'nt':
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_generation(folder, receipt, exports):
    """Stage a complete immutable generation, expose only via committed SQLite.

    Orphaned generations remain invisible after a SQL rollback and may be
    reused only when all files match the same canonical receipt.
    """
    hosting.storage_guard(BLOB, reserve=sum(len(content.encode('utf-8')) for content in exports.values()))
    root = folder / 'results'
    if folder.is_symlink() or root.is_symlink():
        raise OSError('Unsafe result storage directory')
    created = not root.is_dir()
    root.mkdir(exist_ok=True)
    if created:
        # The new results directory entry must survive a power failure before
        # SQLite can commit a pointer to a generation inside it.
        _fsync_directory(folder)
    target = root / receipt
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise OSError('Invalid result generation')
        existing = {p.name for p in target.iterdir()}
        expected = {'transcript.' + name for name in exports}
        if existing != expected or any((target / ('transcript.' + fmt)).read_text(encoding='utf-8') != content
                                       for fmt, content in exports.items()):
            raise OSError('Result generation content mismatch')
        return
    staging = root / ('.stage-' + secrets.token_hex(16))
    staging.mkdir()
    try:
        for fmt, content in exports.items():
            with (staging / ('transcript.' + fmt)).open('w', encoding='utf-8') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        _fsync_directory(staging)
        staging.replace(target)
        _fsync_directory(root)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


@app.get('/api/jobs/{jid}/download/{fmt}')
def download(jid: str, fmt: str, own=Depends(owner)):
    r = owned(jid,own)
    if fmt not in ('txt','md','srt','json') or r['status'] != 'done' or (fmt=='srt' and r['timing']=='none'):
        raise HTTPException(404, 'Resultado indisponível.')
    path = artifact_path(r,fmt)
    if not path.is_file(): raise HTTPException(404, 'Formato indisponível para este motor.')
    return FileResponse(path, filename='transcricao.'+fmt, media_type='application/json' if fmt=='json' else 'text/plain; charset=utf-8')

@app.get('/api/jobs/{jid}')
def job(jid: str, own=Depends(owner)):
    r = owned(jid,own)
    result = public(r)
    if r['status']=='done':
        path=artifact_path(r,'txt')
        try:
            result['text'] = path.read_text(encoding='utf-8')
        except OSError:
            raise HTTPException(503, 'Resultado temporariamente indisponível.')
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
          workers=[dict(r) | {'info':json.loads(r['info']),'enabled':worker_enabled(c,r['id'])} for r in c.execute('SELECT * FROM workers')],
          jobs=[admin_job(r) for r in c.execute("SELECT * FROM jobs WHERE status IN ('queued','running','uploading') ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END,priority DESC,created,id")],
          counts={r['status']:r['n'] for r in c.execute('SELECT status,count(*) n FROM jobs GROUP BY status')},
          totals=dict(c.execute("SELECT count(*) jobs,coalesce(sum(duration),0) audio_seconds,coalesce(sum(elapsed),0) processing_seconds,coalesce(sum(input_bytes),0) bytes FROM jobs").fetchone()),
          host=host_snapshot,telemetry_error=telemetry_error,now=time.time(),versions=obs.package_versions(),
          models=MODELS,catalog=installed_models(getenv('STT_MODELS',str(ROOT/'models'))),
          preloads=[dict(r) for r in c.execute('SELECT * FROM model_preloads ORDER BY updated DESC LIMIT 100')],
          ssh_targets=ssh_targets(),s7_formats=['wav','mp3','ogg','m4a'],
          autonomous_host=(getenv('STT_LIGHT_HOST','0')=='1'),
          s7_side_worker=(getenv('STT_S7_ENABLED','0')=='1' and getenv('STT_S7_SIDE_WORKER','0')=='1'))

def admin_job(r):
    data = {k:r[k] for k in ('id','title','status','outcome','created','started','finished','expires','heartbeat',
        'client_tag','network_tag','ui_language','priority','stage','attempts','worker','model','device','compute',
        'duration','elapsed','cpu_seconds','peak_rss_mb','peak_vram_mb','input_bytes','language','error','model_revision','runtime','engine','gpu_name','progress','timing','result_state','purged_at','purge_attempts','purge_retry_at','purge_error')}
    data['metrics'] = json.loads(r['metrics_json']) if r['metrics_json'] else None
    data['s7_eligible'] = s7_eligible(r)
    data['queue_seconds'] = max(0,r['started']-r['created']) if r['started'] is not None else None
    data['job_total_seconds'] = max(0,r['finished']-r['created']) if r['finished'] is not None else None
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
        count=c.execute('SELECT count(*) FROM jobs WHERE '+where,params).fetchone()[0]  # nosec B608: where uses only hardcoded clauses; values bound
        rows=c.execute('SELECT * FROM jobs WHERE '+where+' ORDER BY created DESC,id LIMIT ? OFFSET ?',params+[limit,(page-1)*limit]).fetchall()  # nosec B608: all user input parameterized
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


def worker_enabled(connection, identity):
    row=connection.execute('SELECT enabled FROM worker_controls WHERE id=?',(identity,)).fetchone()
    return row is None or bool(row['enabled'])


class WorkerPower(BaseModel):
    enabled: bool


@app.put('/api/admin/workers/{identity}/power',dependencies=[Depends(admin)])
def worker_power(identity: str,value: WorkerPower):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM workers WHERE id=?',(identity,)).fetchone():
            raise HTTPException(404,'Executor não encontrado.')
        c.execute('INSERT INTO worker_controls VALUES (?,?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled',(identity,int(value.enabled)))
        if not value.enabled:
            for job in c.execute("SELECT id FROM jobs WHERE status='running' AND worker=?",(identity,)).fetchall():
                c.execute("UPDATE jobs SET status='queued',stage='queued',worker=NULL,lease=NULL,heartbeat=NULL,started=NULL,finished=NULL,progress=0,error=NULL WHERE id=?",(job['id'],))
                obs.event(c,job['id'],'executor_disabled','admin',{'worker':identity})
            c.execute("UPDATE model_preloads SET status='pending',updated=? WHERE worker=? AND status='running'",(time.time(),identity))
        obs.event(c,None,'worker_power','admin',{'worker':identity,'enabled':value.enabled})
    return {'enabled':value.enabled}


@app.post('/api/admin/jobs/{jid}/retry',dependencies=[Depends(admin)])
def retry_job(jid: str):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row:raise HTTPException(404,'Trabalho não encontrado.')
        retryable=row['status']=='failed' or row['status']=='done' and row['result_state']=='partial'
        if not retryable or row['expires']<=time.time():
            raise HTTPException(409,'Só é possível repetir falhas ou resultados parciais ainda válidos.')
        if row['source']=='upload' and not (BLOB/jid/'input.media').is_file():
            raise HTTPException(409,'Arquivo original indisponível.')
        c.execute("UPDATE jobs SET status='queued',outcome=NULL,stage='queued',worker=NULL,lease=NULL,heartbeat=NULL,started=NULL,finished=NULL,progress=0,error=NULL,elapsed=NULL,duration=NULL WHERE id=?",(jid,))
        obs.event(c,jid,'retried','admin')
    return {'ok':True}

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
        supported = {
            'cpu': ('int8','int16','float32','int8_float32'),
            'cuda': ('float16','int8_float16','float32','int8','bfloat16','int8_bfloat16')
        }
        valid = s.model in MODELS and s.compute in supported.get(s.device,())
        if valid and (s.device,s.compute) not in (
                ('cpu','int8'),('cpu','float32'),('cuda','float16'),('cuda','int8_float16')):
            with db() as c:
                row=c.execute('SELECT info FROM workers WHERE id=?',(s.worker,)).fetchone()
            info=json.loads(row['info']) if row else {}
            valid=s.compute in info.get('compute_types',{}).get(s.device,[])
    elif s.engine=='qwen3':
        from server.qwen_engine import RECIPES
        valid = s.model in RECIPES and s.device=='cuda' and s.compute in ('float16','bfloat16')
    else:
        valid = False
    if not valid or not s.language_hint.replace('-','').isalpha():
        raise HTTPException(400,'Combinação de motor, modelo, idioma, dispositivo ou precisão inválida.')
    # A configured SSH host must never be mistaken for a live ASR worker.
    # Until the S7 reports compatible CPU compute and a prepared Whisper model,
    # selecting it would strand public jobs in the queue.
    if s.worker == 's7-edge':
        if getenv('STT_S7_ENABLED','0') != '1':
            raise HTTPException(409,'S7 desativado: configure STT_S7_ENABLED=1 após homologação.')
        with db() as c:
            row = c.execute('SELECT seen,info FROM workers WHERE id=?',(s.worker,)).fetchone()
        info = json.loads(row['info']) if row else {}
        advertised_models = info.get('models',[]) if isinstance(info.get('models'),list) else []
        prepared = any(isinstance(m,dict) and m.get('id') == s.model for m in advertised_models)
        cpu_types = info.get('compute_types',{})
        cpu_types = cpu_types.get('cpu',[]) if isinstance(cpu_types,dict) else []
        if (not row or time.time()-row['seen'] >= 90 or s.engine != 'whisper'
                or info.get('engine') != 'whisper.cpp' or s.model != 'tiny'
                or s.device != 'cpu' or s.compute != 'float32'
                or s.compute not in cpu_types or not prepared):
            raise HTTPException(409,'S7 Edge apenas acessível por SSH: falta executor STT online com modelo e CPU compatíveis.')
    with db() as c:
        c.execute('UPDATE settings SET value=? WHERE id=1', (s.model_dump_json(),))
        obs.event(c,None,'settings_changed','admin',s.model_dump())
    return s



class ModelPreloadRequest(BaseModel):
    worker: str = Field(min_length=1,max_length=80)
    model: str

class ModelPreloadClaim(BaseModel):
    worker: str = Field(min_length=1,max_length=80)

class ModelPreloadReport(BaseModel):
    worker: str = Field(min_length=1,max_length=80)
    status: str
    revision: str | None = Field(default=None,max_length=80)
    error: str | None = Field(default=None,max_length=80)

def valid_worker_name(value):
    return bool(value) and all(ch in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for ch in value)

@app.post('/api/admin/model-preloads',dependencies=[Depends(admin)],status_code=202)
def request_model_preload(value: ModelPreloadRequest):
    if value.worker == 's7-edge':
        raise HTTPException(409,'S7 usa pesos GGML instalados pelo operador; não aceita preload remoto.')
    if value.model not in MODELS or not valid_worker_name(value.worker):
        raise HTTPException(400,'Executor ou modelo inválido.')
    now=time.time()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not worker_enabled(c,value.worker):
            raise HTTPException(409,'Executor desligado.')
        if not c.execute('SELECT 1 FROM workers WHERE id=?',(value.worker,)).fetchone():
            raise HTTPException(404,'Executor ainda não registrado.')
        existing=c.execute("""SELECT * FROM model_preloads WHERE worker=? AND model=?
                AND status IN ('pending','running') ORDER BY created DESC LIMIT 1""",
                           (value.worker,value.model)).fetchone()
        if existing:
            return dict(existing)
        operation=secrets.token_hex(16)
        c.execute('INSERT INTO model_preloads VALUES(?,?,?,?,?,?,?,?)',
                  (operation,value.worker,value.model,'pending',now,now,None,None))
        return dict(id=operation,worker=value.worker,model=value.model,status='pending',
                    created=now,updated=now,revision=None,error=None)

@app.post('/api/worker/model-preloads/claim',dependencies=[Depends(worker)])
def claim_model_preload(value: ModelPreloadClaim, request: Request):
    require_s7_scope(request,value.worker)
    if value.worker == 's7-edge':
        return {'operation':None}  # GGML weights are provisioned offline by the operator.
    if not valid_worker_name(value.worker):
        raise HTTPException(400,'Executor inválido.')
    now=time.time()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not worker_enabled(c,value.worker):return {'operation':None}
        if c.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            return {'operation':None}
        op=c.execute("""SELECT * FROM model_preloads WHERE worker=? AND status='pending'
                        ORDER BY created LIMIT 1""",(value.worker,)).fetchone()
        if not op:
            return {'operation':None}
        c.execute("UPDATE model_preloads SET status='running',updated=? WHERE id=?",(now,op['id']))
        return {'operation':dict(op)|{'status':'running','updated':now}}

@app.post('/api/worker/model-preloads/{pid}/report',dependencies=[Depends(worker)])
def report_model_preload(pid: str,value: ModelPreloadReport, request: Request):
    require_s7_scope(request,value.worker)
    if value.worker == 's7-edge':
        raise HTTPException(409,'O S7 usa modelos GGML geridos localmente.')
    if len(pid)!=32 or any(ch not in '0123456789abcdef' for ch in pid):
        raise HTTPException(404,'Operação não encontrada.')
    if not valid_worker_name(value.worker):
        raise HTTPException(400,'Executor inválido.')
    if value.status not in ('running','ready','failed'):
        raise HTTPException(400,'Estado inválido.')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not worker_enabled(c,value.worker):raise HTTPException(409,'Executor desligado.')
        op=c.execute('SELECT * FROM model_preloads WHERE id=? AND worker=?',
                     (pid,value.worker)).fetchone()
        if not op:
            raise HTTPException(404,'Operação não encontrada.')
        if op['status'] in ('ready','failed'):
            return {'ok':True,'status':op['status']}
        if op['status']!='running':
            raise HTTPException(409,'Operação não iniciada.')
        c.execute('UPDATE model_preloads SET status=?,updated=?,revision=?,error=? WHERE id=?',
                  (value.status,time.time(),value.revision if value.status=='ready' else None,
                   value.error if value.status=='failed' else None,pid))
    return {'ok':True,'status':value.status}


class Claim(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    info: dict = Field(default_factory=dict)


class WorkerControl(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    applied_enabled: bool | None = None
    ready: bool | None = None
    reason: str | None = Field(default=None,pattern='^(WORKSPACE_UNAVAILABLE|STORAGE_UNAVAILABLE|OUTBOX_PENDING)$')


@app.post('/api/worker/control',dependencies=[Depends(worker)])
def worker_control(value: WorkerControl,request: Request):
    require_s7_scope(request,value.id)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        enabled=worker_enabled(c,value.id)
        row=c.execute('SELECT info FROM workers WHERE id=?',(value.id,)).fetchone()
        info=json.loads(row['info']) if row else {}
        info['power_control_supported']=True
        if value.ready is not None:
            info['ready']=value.ready
            info['readiness_reason']=value.reason if not value.ready else None
        if value.applied_enabled is not None and value.applied_enabled==enabled:
            info['power_state']='enabled' if enabled else 'disabled'
        elif not enabled:info['power_state']='stopping'
        c.execute('INSERT OR REPLACE INTO workers VALUES(?,?,?)',(value.id,time.time(),json.dumps(info)))
    return {'enabled':enabled}

def s7_eligible(item):
    return (item['source']=='upload' and 0 < (item['input_bytes'] or 0) <= 8*1024**2
            and ('duration' not in item.keys() or item['duration'] is None or item['duration']<=300)
            and Path(item['title'] or '').suffix.lower() in ('.wav','.mp3','.ogg','.m4a'))

@app.post('/api/worker/claim', dependencies=[Depends(worker)])
def claim(w: Claim, request: Request):
    require_s7_scope(request,w.id)
    now = time.time()
    info=json.dumps(w.info)
    if len(info)>8000: raise HTTPException(413,'Metadados do executor excedem o limite.')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        previous=c.execute('SELECT info FROM workers WHERE id=?',(w.id,)).fetchone()
        readiness={k:v for k,v in json.loads(previous['info']).items() if k in ('ready','readiness_reason')} if previous else {}
        c.execute('INSERT OR REPLACE INTO workers VALUES (?,?,?)', (w.id,now,json.dumps(dict(w.info,**readiness))))
        if not worker_enabled(c,w.id):return {'job':None,'enabled':False}
        if readiness.get('ready') is False:return {'job':None,'ready':False}
        cfg = json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
        side_s7 = (w.id == 's7-edge' and cfg['worker'] != 's7-edge'
                   and getenv('STT_S7_SIDE_WORKER','0')=='1')
        if cfg['paused'] or (cfg['worker'] != w.id and not side_s7):
            return {'job':None}
        if side_s7:
            # Explicit opt-in: S7 runs Tiny on small audio while the primary
            # worker retains its own model/compute policy for other jobs.
            cfg = dict(cfg,worker='s7-edge',model='tiny',device='cpu',
                       compute='float32',engine='whisper',language_hint='auto')
        if w.id == 's7-edge':
            # Opt-in stays off until the operator has provisioned the scoped key
            # and verified live inference. No SSH-only entry ever claims work.
            if getenv('STT_S7_ENABLED','0') != '1':
                return {'job':None}
            models = w.info.get('models',[])
            types = w.info.get('compute_types',{})
            cpu_types = types.get('cpu',[]) if isinstance(types,dict) else []
            if (w.info.get('engine') != 'whisper.cpp' or cfg.get('engine','whisper') != 'whisper'
                    or cfg['device'] != 'cpu' or cfg['model'] != 'tiny'
                    or cfg['compute'] != 'float32' or 'float32' not in cpu_types
                    or not isinstance(models,list) or not any(isinstance(m,dict) and m.get('id')=='tiny' for m in models)):
                return {'job':None}
        if cfg.get('engine','whisper')=='qwen3' and cfg['model'] not in w.info.get('qwen_models',[]):
            return {'job':None}
        if c.execute("SELECT 1 FROM jobs WHERE status='running' AND worker=?",(w.id,)).fetchone():
            return {'job':None}
        candidates = c.execute("SELECT * FROM jobs WHERE status='queued' AND expires>? ORDER BY priority DESC,created,id LIMIT 20",(now,)).fetchall()
        if w.id == 's7-edge':
            # The phone only claims eligible short uploaded audio.
            r = next((item for item in candidates if s7_eligible(item)),None)
        else:
            r = candidates[0] if candidates else None
            if (r and getenv('STT_S7_ENABLED','0')=='1'
                    and getenv('STT_S7_SIDE_WORKER','0')=='1'
                    and cfg['worker']==w.id):
                other = c.execute("SELECT seen,info FROM workers WHERE id='s7-edge'").fetchone()
                active = False
                if other and now-other['seen']<90:
                    try:
                        side_caps=json.loads(other['info'])
                        active=(side_caps.get('engine')=='whisper.cpp'
                                and 'float32' in side_caps.get('compute_types',{}).get('cpu',[])
                                and any(m.get('id')=='tiny' for m in side_caps.get('models',[]) if isinstance(m,dict)))
                    except (ValueError,TypeError,AttributeError):
                        active=False
                if active:
                    # Prefer different jobs for the primary worker while S7
                    # is online; fall back if there are no such jobs.
                    r = next((item for item in candidates if not s7_eligible(item)),r)
        if not r: return {'job':None}
        lease = secrets.token_hex(32)
        gpu = w.info.get('gpu') if isinstance(w.info.get('gpu'),dict) else None
        gpu_name = str(gpu.get('name',''))[:120] if gpu and cfg['device']=='cuda' else None
        c.execute("UPDATE jobs SET status='running',started=?,worker=?,model=?,device=?,compute=?,engine=?,gpu_name=?,lease=?,heartbeat=?,progress=0,attempts=attempts+1,error=NULL WHERE id=?", (now,w.id,cfg['model'],cfg['device'],cfg['compute'],cfg.get('engine','whisper'),gpu_name,lease,now,r['id']))
        c.execute("UPDATE jobs SET stage='preparing' WHERE id=?",(r['id'],))
        obs.event(c,r['id'],'started',w.id,dict(model=cfg['model'],device=cfg['device'],compute=cfg['compute']))
        extra = ({'input_type':Path(r['title'] or '').suffix.lower().lstrip('.')}
                 if w.id == 's7-edge' else {})
        return {'job':dict(id=r['id'],source=r['source'],lease=lease,expires=r['expires'],**cfg,**extra)}

def leased(c,jid,lease,scope=None):
    r = c.execute("SELECT * FROM jobs WHERE id=? AND lease=? AND status='running' AND expires>?", (jid,lease,time.time())).fetchone()
    if not r: raise HTTPException(409,'Lease expirado.')
    if scope is not None and r['worker'] != scope:
        raise HTTPException(403,'Chave restrita a outro executor.')
    return r

@app.get('/api/worker/{jid}/input', dependencies=[Depends(worker)])
def worker_input(jid: str, request: Request):
    with db() as c: leased(c,jid,request.headers.get('x-lease',''),getattr(request.state,'worker_scope',None))
    p = BLOB / jid / 'input.media'
    if not p.exists(): raise HTTPException(404,'Entrada indisponível.')
    return FileResponse(p)

class Heartbeat(BaseModel):
    lease: str
    progress: float = Field(default=0,ge=0,le=100)
    stage: str = Field(default='preparing',pattern='^(preparing|downloading|loading|transcribing|finishing)$')
    runtime: dict = Field(default_factory=dict)

@app.post('/api/worker/{jid}/heartbeat', dependencies=[Depends(worker)])
def heartbeat(jid: str, h: Heartbeat, request: Request):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        r = leased(c,jid,h.lease,getattr(request.state,'worker_scope',None))
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


def timestamp(n):
    ms = int(n*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

@app.post('/api/worker/{jid}/result', dependencies=[Depends(worker)])
def result(jid: str, result: Result, request: Request):
    receipt = result_receipt(result)
    lease_hash = hashlib.sha256(result.lease.encode()).hexdigest()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        scope = getattr(request.state,'worker_scope',None)
        previous = c.execute('SELECT status,expires,receipt_hash,receipt_lease,worker FROM jobs WHERE id=?',(jid,)).fetchone()
        if previous and scope is not None and previous['worker'] != scope:
            raise HTTPException(403,'Chave restrita a outro executor.')
        if previous and previous['expires']>time.time() and previous['status'] in ('done','failed'):
            if previous['receipt_hash']==receipt and previous['receipt_lease']==lease_hash:
                return {'ok':True,'receipt':receipt,'duplicate':True}
            raise HTTPException(409,'Tentativa concluída com resultado diferente.')
        r = leased(c,jid,result.lease,scope)
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
            _publish_generation(folder,receipt,exports)
        state = 'failed' if result.error else 'done'
        outcome = 'failed' if result.error else ('partial' if result.result_state=='partial' else 'done')
        c.execute("""UPDATE jobs SET status=?,finished=?,duration=?,elapsed=?,cpu_seconds=?,peak_rss_mb=?,
                   peak_vram_mb=?,language=?,error=?,progress=?,lease=NULL,input_bytes=max(input_bytes,?),
                   receipt_hash=?,receipt_lease=?,result_state=?,timing=?,artifact_layout=? WHERE id=? AND lease=? AND status='running'""",
                  (state,time.time(),result.duration,result.elapsed,result.cpu_seconds,result.peak_rss_mb,
                   result.peak_vram_mb,result.language,result.error,0 if result.error else 100,result.input_bytes,
                   receipt,lease_hash,result.result_state,result.timing,
                    'generations-v1' if not result.error else None,jid,result.lease))
        runtime=json.dumps(result.runtime)
        if len(runtime)>8000: runtime=json.dumps({'metadata_error':'oversized_runtime'})
        c.execute('UPDATE jobs SET outcome=?,stage=?,runtime=?,model_revision=?,metrics_json=? WHERE id=?',
                  (outcome,state,runtime,result.model_revision,json.dumps(result.metrics) if result.metrics is not None else None,jid))
        if result.title and r['source']!='upload': c.execute('UPDATE jobs SET title=? WHERE id=?',(result.title,jid))
        obs.event(c,jid,outcome,r['worker'],{'elapsed':result.elapsed})
    return {'ok':True,'receipt':receipt,'duplicate':False}

admin_auth.install(app, db, getenv)
app.mount('/', StaticFiles(directory=ROOT/'web',html=True),name='web')
