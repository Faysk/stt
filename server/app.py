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
from server.catalog import MODEL_CATALOG, installed_models
from server import observability as obs

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
        columns={r[1] for r in c.execute('PRAGMA table_info(jobs)')}
        for name,definition in {
            'client_tag':'TEXT','network_tag':'TEXT','ui_language':'TEXT',
            'priority':'INTEGER DEFAULT 0','outcome':'TEXT','runtime':'TEXT',
            'model_revision':'TEXT','stage':"TEXT DEFAULT 'queued'"}.items():
            if name not in columns: c.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
        c.execute("UPDATE jobs SET outcome=status WHERE outcome IS NULL AND status IN ('done','failed','cancelled')")
        c.execute("UPDATE jobs SET stage=status WHERE stage='queued' AND status IN ('done','failed','expired','cancelled')")
        # Backfill an anonymous administrative label; this is not an access credential.
        for row in c.execute("SELECT id,owner FROM jobs WHERE client_tag IS NULL AND owner!=''").fetchall():
            c.execute('UPDATE jobs SET client_tag=? WHERE id=?',(client_tag(row['owner']),row['id']))
        obs.install(c)
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
        c.execute("UPDATE jobs SET status='expired',stage='expired',owner='',title=NULL,source=NULL,error=NULL,lease=NULL,language=NULL WHERE expires<=?", (now,))
        for row in c.execute("SELECT id,attempts FROM jobs WHERE status='running' AND heartbeat<? AND expires>?",(now-180,now)).fetchall():
            failed=row['attempts']>=3
            c.execute("UPDATE jobs SET status=?,outcome=?,stage=?,error=?,lease=NULL WHERE id=?",('failed' if failed else 'queued','failed' if failed else None,'failed' if failed else 'queued','Executor desconectado; limite de tentativas atingido.' if failed else None,row['id']))
            c.execute('UPDATE jobs SET finished=? WHERE id=?',(now if failed else None,row['id']))
            obs.event(c,row['id'],'failed' if failed else 'requeued',data={'reason':'heartbeat_timeout'})
        stale = c.execute("SELECT id FROM jobs WHERE status='uploading' AND created<?",(now-900,)).fetchall()
        for row in stale:
            shutil.rmtree(BLOB/row['id'],ignore_errors=True)
            obs.event(c,row['id'],'failed',data={'reason':'upload_timeout'})
        c.execute("UPDATE jobs SET status='failed',outcome='failed',stage='failed',finished=?,error='Envio interrompido.' WHERE status='uploading' AND created<?",(now,now-900))
        c.execute('DELETE FROM admission WHERE created<?',(now-3600,))

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
    response = await call_next(request)
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

def admit(request: Request):
    ip = request.headers.get('x-tts-client') if os.getenv('TTS_REQUIRE_EDGE')=='1' else request.client.host
    key = hashlib.sha256((os.getenv('TTS_EDGE_KEY','local')+(ip or 'unknown')).encode()).hexdigest()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT count(*) FROM admission WHERE client=? AND created>?',(key,time.time()-3600)).fetchone()[0]>=20:
            raise HTTPException(429,'Limite de envios desta rede atingido. Tente mais tarde.')
        c.execute('INSERT INTO admission VALUES (?,?)',(key,time.time()))

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
    if c.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','uploading','running')").fetchone()[0] >= 20:
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

@app.post('/api/jobs/upload', status_code=201, dependencies=[Depends(admit)])
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

@app.post('/api/jobs/youtube', status_code=201, dependencies=[Depends(admit)])
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
    with db() as c:
        return dict(settings=json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0]),
          workers=[dict(r) | {'info':json.loads(r['info'])} for r in c.execute('SELECT * FROM workers')],
          jobs=[admin_job(r) for r in c.execute("SELECT * FROM jobs WHERE status IN ('queued','running','uploading') ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END,priority DESC,created,id")],
          counts={r['status']:r['n'] for r in c.execute('SELECT status,count(*) n FROM jobs GROUP BY status')},
          totals=dict(c.execute("SELECT count(*) jobs,coalesce(sum(duration),0) audio_seconds,coalesce(sum(elapsed),0) processing_seconds,coalesce(sum(input_bytes),0) bytes FROM jobs").fetchone()),
          host=host_snapshot,telemetry_error=telemetry_error,now=time.time(),versions=obs.package_versions(),
          models=MODELS,catalog=installed_models(os.getenv('TTS_MODELS',str(ROOT/'models'))))

def admin_job(r):
    return {k:r[k] for k in ('id','title','status','outcome','created','started','finished','expires','heartbeat',
        'client_tag','network_tag','ui_language','priority','stage','attempts','worker','model','device','compute',
        'duration','elapsed','cpu_seconds','peak_rss_mb','peak_vram_mb','input_bytes','language','error','model_revision','runtime','progress')}

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
    if s.model not in MODELS or (s.device,s.compute) not in [('cpu','int8'),('cpu','float32'),('cuda','float16'),('cuda','int8_float16')]:
        raise HTTPException(400,'Combinação de modelo, dispositivo ou precisão inválida.')
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
        if c.execute("SELECT 1 FROM jobs WHERE status='running' AND worker=?",(w.id,)).fetchone():
            return {'job':None}
        r = c.execute("SELECT * FROM jobs WHERE status='queued' AND expires>? ORDER BY priority DESC,created,id LIMIT 1", (now,)).fetchone()
        if not r: return {'job':None}
        lease = secrets.token_hex(32)
        c.execute("UPDATE jobs SET status='running',started=?,worker=?,model=?,device=?,compute=?,lease=?,heartbeat=?,attempts=attempts+1,error=NULL WHERE id=?", (now,w.id,cfg['model'],cfg['device'],cfg['compute'],lease,now,r['id']))
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
        r = leased(c,jid,h.lease)
        if r['stage']!=h.stage: obs.event(c,jid,'stage',r['worker'],{'stage':h.stage})
        c.execute('UPDATE jobs SET heartbeat=?,progress=?,stage=? WHERE id=?',(time.time(),h.progress,h.stage,jid))
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
    runtime: dict = Field(default_factory=dict)
    model_revision: str | None = Field(default=None,max_length=80)
    title: str | None = Field(default=None,max_length=180)

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
        outcome='failed' if result.error else 'done'
        runtime=json.dumps(result.runtime)
        if len(runtime)>8000: runtime=json.dumps({'metadata_error':'oversized_runtime'})
        c.execute('UPDATE jobs SET outcome=?,stage=?,runtime=?,model_revision=? WHERE id=?',(outcome,outcome,runtime,result.model_revision,jid))
        if result.title and r['source']!='upload': c.execute('UPDATE jobs SET title=? WHERE id=?',(result.title,jid))
        obs.event(c,jid,outcome,r['worker'],{'elapsed':result.elapsed})
    return {'ok':True}

app.mount('/', StaticFiles(directory=ROOT/'web',html=True),name='web')
