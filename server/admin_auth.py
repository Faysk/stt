"""Single-owner WebAuthn approval. A public QR is never a login credential."""
import base64
import hashlib
import hmac
import io
import json
import secrets
import time

import qrcode
import qrcode.image.svg
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from webauthn import (generate_authentication_options, generate_registration_options,
                      options_to_json, verify_authentication_response,
                      verify_registration_response)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement)

ORIGIN = 'https://stt.faysk.dev'
RP_ID = 'stt.faysk.dev'
USER_ID = b'stt-single-owner-v1'
DESKTOP = '__Host-stt_qr'
PHONE = '__Host-stt_ceremony'
SESSION = '__Host-stt_admin'
QR_TTL = 90
CEREMONY_TTL = 120
IDLE_TTL = 1800
SESSION_TTL = 8 * 3600


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def csrf(token):
    return hmac.new(token.encode(), b'stt-csrf-v1', hashlib.sha256).hexdigest()


def install_schema(c):
    # Additive, independently versioned; no changes to jobs/outbox or their keys.
    c.executescript('''
    CREATE TABLE IF NOT EXISTS admin_auth_migrations(version INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS admin_auth_credentials(
      id TEXT PRIMARY KEY, public_key BLOB NOT NULL, sign_count INTEGER NOT NULL,
      created REAL NOT NULL, last_used REAL, device_type TEXT, backed_up INTEGER);
    CREATE TABLE IF NOT EXISTS admin_auth_pairing(
      token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS admin_auth_requests(
      id TEXT PRIMARY KEY, browser_hash TEXT UNIQUE NOT NULL, code TEXT NOT NULL,
      context TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
      expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS admin_auth_challenges(
      id TEXT PRIMARY KEY, binding_hash TEXT NOT NULL, challenge BLOB NOT NULL,
      purpose TEXT NOT NULL, target TEXT NOT NULL, decision TEXT NOT NULL,
      expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS admin_auth_sessions(
      token_hash TEXT PRIMARY KEY, credential_id TEXT NOT NULL,
      created REAL NOT NULL, seen REAL NOT NULL, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS admin_auth_rates(
      key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS admin_auth_events(
      id INTEGER PRIMARY KEY, at REAL NOT NULL, event TEXT NOT NULL);
    INSERT OR IGNORE INTO admin_auth_migrations VALUES(1);
    ''')


def cleanup(c, now=None):
    now = time.time() if now is None else now
    c.execute("UPDATE admin_auth_requests SET state='expired' WHERE expires<=? AND state IN ('pending','scanned','approved')", (now,))
    c.execute('DELETE FROM admin_auth_requests WHERE expires<?', (now - 600,))
    c.execute('DELETE FROM admin_auth_pairing WHERE expires<=?', (now,))
    c.execute('DELETE FROM admin_auth_challenges WHERE expires<=?', (now,))
    c.execute('DELETE FROM admin_auth_rates WHERE expires<=?', (now,))
    c.execute('DELETE FROM admin_auth_sessions WHERE expires<=? OR seen<=?', (now, now-IDLE_TTL))
    c.execute('DELETE FROM admin_auth_events WHERE at<?', (now-30*86400,))


def event(c, name):
    # Fixed vocabulary only: no keys, challenge/assertion, user agent or IP.
    c.execute('INSERT INTO admin_auth_events(at,event) VALUES(?,?)', (time.time(), name))


def cookie(response, name, token, ttl):
    response.set_cookie(name, token, max_age=ttl, secure=True, httponly=True,
                        samesite='strict', path='/')


def clear_cookie(response, name):
    response.delete_cookie(name, secure=True, httponly=True, samesite='strict', path='/')


def same_origin(request):
    if request.headers.get('origin') != ORIGIN or request.headers.get('sec-fetch-site') not in (None, 'same-origin'):
        raise HTTPException(403, 'Origem de autenticação inválida.')
    if request.headers.get('x-stt-auth') != '1' or request.headers.get('content-type', '').split(';')[0] != 'application/json':
        raise HTTPException(403, 'Pedido de autenticação inválido.')


def check_csrf(request, token):
    if not token or not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), csrf(token).encode()):
        raise HTTPException(403, 'Atualize a página e tente novamente.')


async def payload(request):
    # Bound chunked bodies too, before JSON decode or crypto verification.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 32768:
            raise HTTPException(413, 'Pedido de autenticação muito grande.')
        body.extend(chunk)
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, 'Pedido inválido.') from None


def limit(db, getenv, request, operation, maximum):
    # Only trust an edge-injected address after the origin guard verified EDGE_KEY.
    address = request.headers.get('x-stt-client', 'unknown') if getenv('STT_REQUIRE_EDGE') == '1' else (request.client.host if request.client else 'unknown')
    identity = hmac.new(getenv('STT_EDGE_KEY', 'local-auth-rate').encode(), address.encode(), hashlib.sha256).hexdigest()
    now = time.time()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        cleanup(c, now)
        for key, cap in ((operation+':'+identity, maximum), (operation+':global', maximum*20)):
            row = c.execute('SELECT count FROM admin_auth_rates WHERE key=?', (key,)).fetchone()
            if row and row['count'] >= cap:
                raise HTTPException(429, 'Muitas tentativas. Aguarde um minuto.')
            c.execute('INSERT INTO admin_auth_rates VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, now+60))


def enabled(getenv):
    return getenv('STT_ADMIN_QR_ENABLED', '0') == '1'


def legacy_enabled(getenv):
    return getenv('STT_ADMIN_LEGACY_ENABLED', '1') == '1'


def authorize(request, db, getenv):
    """Called by every existing Depends(admin), preserving worker/public scopes."""
    supplied = request.headers.get('x-admin-key', '')
    expected = getenv('STT_ADMIN_KEY', '')
    if legacy_enabled(getenv) and expected and supplied and hmac.compare_digest(supplied.encode(), expected.encode()):
        return
    if not enabled(getenv):
        raise HTTPException(401, 'Autenticação necessária.')
    token = request.cookies.get(SESSION, '')
    now = time.time()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('''SELECT s.* FROM admin_auth_sessions s JOIN admin_auth_credentials k
                           ON k.id=s.credential_id WHERE token_hash=?''', (digest(token),)).fetchone()
        if not token or not row or row['expires'] <= now or row['seen'] <= now-IDLE_TTL:
            raise HTTPException(401, 'Sessão encerrada. Entre novamente.')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            same_origin(request)
            check_csrf(request, token)
        c.execute('UPDATE admin_auth_sessions SET seen=? WHERE token_hash=?', (now, digest(token)))
    request.state.admin_credential = row['credential_id']


def revoke_all(c):
    """Called only from local recovery/revocation inside an explicit transaction."""
    c.execute('DELETE FROM admin_auth_credentials')
    c.execute('DELETE FROM admin_auth_sessions')
    c.execute('DELETE FROM admin_auth_requests')
    c.execute('DELETE FROM admin_auth_challenges')
    c.execute('DELETE FROM admin_auth_pairing')


def new_pairing(db, recover=False):
    """Local CLI only. Recover revokes all credentials, sessions and open requests."""
    token = secrets.token_urlsafe(32)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM admin_auth_credentials').fetchone() and not recover:
            raise ValueError('Já existe passkey. Use recuperação local explícita.')
        if recover:
            revoke_all(c)
            event(c, 'local_recovery')
        c.execute('DELETE FROM admin_auth_pairing')
        c.execute('INSERT INTO admin_auth_pairing VALUES(?,?)', (digest(token), time.time()+300))
        event(c, 'pairing_opened')
    # Fragment never reaches an HTTP GET/access log. Client removes it immediately.
    return ORIGIN+'/admin-approve.html#pair='+token


def install(app, db, getenv):
    prefix = '/api/admin/auth'

    def require_enabled(request, operation, maximum=20):
        if not enabled(getenv):
            raise HTTPException(404, 'Entrada por celular indisponível.')
        same_origin(request)
        limit(db, getenv, request, operation, maximum)

    def desktop_request(c, request, rid):
        token = request.cookies.get(DESKTOP, '')
        check_csrf(request, token)
        row = c.execute('SELECT * FROM admin_auth_requests WHERE browser_hash=? AND id=?', (digest(token), str(rid))).fetchone()
        if not row:
            raise HTTPException(401, 'Pedido não pertence a este navegador.')
        return row

    def qr_result(row):
        url = ORIGIN+'/admin-approve.html?request='+row['id']
        # Only a server-built same-origin URL, rendered by a local pinned library.
        image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=4)
        out = io.BytesIO()
        image.save(out)
        return dict(request=row['id'], code=row['code'], expires=row['expires'],
                    url=url, svg=out.getvalue().decode(), context=row['context'])

    @app.get(prefix+'/config')
    def config():
        return dict(qr_enabled=enabled(getenv), legacy_enabled=legacy_enabled(getenv))

    @app.post(prefix+'/qr/start')
    async def start(request: Request):
        require_enabled(request, 'start', 10)
        await payload(request)
        now = time.time()
        token = secrets.token_urlsafe(32)
        ua = request.headers.get('user-agent', '')
        browser = 'Edge' if 'Edg/' in ua else 'Chrome' if 'Chrome/' in ua else 'Firefox' if 'Firefox/' in ua else 'Safari' if 'Safari/' in ua else 'Navegador'
        platform = 'Windows' if 'Windows' in ua else 'Android' if 'Android' in ua else 'macOS' if 'Macintosh' in ua else 'dispositivo'
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            old = request.cookies.get(DESKTOP, '')
            if old and c.execute('SELECT 1 FROM admin_auth_requests WHERE browser_hash=?', (digest(old),)).fetchone():
                token = old
                c.execute('DELETE FROM admin_auth_requests WHERE browser_hash=?', (digest(old),))
            if c.execute('SELECT count(*) FROM admin_auth_requests WHERE expires>?', (now,)).fetchone()[0] >= 200:
                raise HTTPException(429, 'Aguarde para gerar outro QR.')
            rid = secrets.token_hex(32)
            c.execute('INSERT INTO admin_auth_requests VALUES(?,?,?,?,?,?,?)',
                      (rid, digest(token), f'{secrets.randbelow(1000000):06}', browser+' · '+platform,
                       'pending', now, now+QR_TTL))
            row = c.execute('SELECT * FROM admin_auth_requests WHERE id=?', (rid,)).fetchone()
            data = qr_result(row)
            event(c, 'qr_started')
        response = JSONResponse(dict(**data, csrf=csrf(token)))
        cookie(response, DESKTOP, token, QR_TTL)
        return response

    @app.post(prefix+'/qr/status')
    async def status(request: Request):
        require_enabled(request, 'poll', 60)
        data = await payload(request)
        with db() as c:
            row = desktop_request(c, request, data.get('request',''))
            return dict(state='expired' if row['expires'] <= time.time() else row['state'])

    @app.post(prefix+'/qr/cancel')
    async def cancel(request: Request):
        require_enabled(request, 'cancel')
        data = await payload(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = desktop_request(c, request, data.get('request',''))
            if row['state'] == 'consumed':
                raise HTTPException(409, 'Acesso já concluído. Use Sair no painel.')
            c.execute("UPDATE admin_auth_requests SET state='cancelled' WHERE id=? AND state!='consumed'", (row['id'],))
        response = JSONResponse(dict(state='cancelled'))
        clear_cookie(response, DESKTOP)
        return response

    @app.post(prefix+'/qr/consume')
    async def consume(request: Request):
        require_enabled(request, 'consume')
        data = await payload(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = desktop_request(c, request, data.get('request',''))
            if row['expires'] <= time.time() or row['state'] != 'approved':
                raise HTTPException(409, 'Pedido ainda não aprovado ou expirado.')
            # Exactly one credential by product policy. Recovery clears all requests.
            credential = c.execute('SELECT id FROM admin_auth_credentials').fetchone()
            if not credential:
                raise HTTPException(401, 'Passkey revogada.')
            token = secrets.token_urlsafe(48)
            now = time.time()
            c.execute('INSERT INTO admin_auth_sessions VALUES(?,?,?,?,?)', (digest(token),credential['id'],now,now,now+SESSION_TTL))
            c.execute("UPDATE admin_auth_requests SET state='consumed' WHERE id=?", (row['id'],))
            event(c, 'session_created')
        response = JSONResponse(dict(authenticated=True, csrf=csrf(token)))
        cookie(response, SESSION, token, SESSION_TTL)
        clear_cookie(response, DESKTOP)
        return response

    @app.get(prefix+'/session')
    def session(request: Request):
        # Session check never accepts a header key and never creates a session.
        if request.headers.get('x-admin-key'):
            raise HTTPException(401, 'Sessão necessária.')
        authorize(request, db, getenv)
        return dict(authenticated=True, csrf=csrf(request.cookies.get(SESSION, '')))

    @app.post(prefix+'/logout')
    async def logout(request: Request):
        require_enabled(request, 'logout')
        authorize(request, db, getenv)
        with db() as c:
            c.execute('DELETE FROM admin_auth_sessions WHERE token_hash=?', (digest(request.cookies.get(SESSION, '')),))
            event(c, 'logout')
        response = JSONResponse(dict(authenticated=False))
        clear_cookie(response, SESSION)
        return response

    @app.post(prefix+'/approve/context')
    async def context(request: Request):
        require_enabled(request, 'context', 30)
        data = await payload(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT * FROM admin_auth_requests WHERE id=? AND expires>? AND state IN ('pending','scanned')", (str(data.get('request', '')), time.time())).fetchone()
            if not row:
                raise HTTPException(404, 'Pedido encerrado ou expirado.')
            c.execute("UPDATE admin_auth_requests SET state='scanned' WHERE id=?", (row['id'],))
            return dict(code=row['code'],context=row['context'],expires=row['expires'])

    @app.post(prefix+'/ceremony/options')
    async def options(request: Request):
        require_enabled(request, 'options', 10)
        data = await payload(request)
        purpose = data.get('purpose')
        target = str(data.get('pair', '')) if purpose == 'enroll' else str(data.get('request', ''))
        decision = data.get('decision', 'approve')
        if purpose not in ('enroll','approve') or decision not in ('approve','reject') or len(target)>256:
            raise HTTPException(400, 'Pedido inválido.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            now = time.time()
            credentials = c.execute('SELECT * FROM admin_auth_credentials').fetchall()
            challenge = secrets.token_bytes(32)
            if purpose == 'enroll':
                target = digest(target)
                pairing = c.execute('SELECT * FROM admin_auth_pairing WHERE token_hash=? AND expires>?', (target, now)).fetchone()
                if not pairing or credentials:
                    raise HTTPException(403, 'Pareamento indisponível. Inicie pelo procedimento local.')
                expires = min(now+CEREMONY_TTL, pairing['expires'])
                public = generate_registration_options(rp_id=RP_ID, rp_name='stt / faysk',
                    user_id=USER_ID, user_name='STT admin', challenge=challenge,
                    authenticator_selection=AuthenticatorSelectionCriteria(
                        resident_key=ResidentKeyRequirement.REQUIRED,
                        user_verification=UserVerificationRequirement.REQUIRED), timeout=120000)
            else:
                row = c.execute("SELECT * FROM admin_auth_requests WHERE id=? AND expires>? AND state IN ('pending','scanned')", (target, now)).fetchone()
                if not row or not credentials:
                    raise HTTPException(403, 'Aprovação indisponível. Verifique o pedido e a passkey.')
                expires = min(now+CEREMONY_TTL, row['expires'])
                public = generate_authentication_options(rp_id=RP_ID,challenge=challenge,
                    allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(k['id'])) for k in credentials],
                    user_verification=UserVerificationRequirement.REQUIRED,timeout=90000)
            binding = secrets.token_urlsafe(32)
            cid = secrets.token_hex(32)
            old = request.cookies.get(PHONE, '')
            c.execute('DELETE FROM admin_auth_challenges WHERE binding_hash=?', (digest(old),))
            if c.execute('SELECT count(*) FROM admin_auth_challenges').fetchone()[0] >= 200:
                raise HTTPException(429, 'Aguarde para tentar novamente.')
            c.execute('INSERT INTO admin_auth_challenges VALUES(?,?,?,?,?,?,?)',
                (cid,digest(binding),challenge,purpose,target,decision,expires))
        response = JSONResponse(dict(id=cid,publicKey=json.loads(options_to_json(public)),csrf=csrf(binding)))
        cookie(response, PHONE, binding, CEREMONY_TTL)
        return response

    @app.post(prefix+'/ceremony/finish')
    async def finish(request: Request):
        require_enabled(request, 'finish', 10)
        data = await payload(request)
        binding = request.cookies.get(PHONE, '')
        check_csrf(request, binding)
        invalid = False
        # Consume even invalid assertions; retries must generate a fresh challenge.
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM admin_auth_challenges WHERE id=? AND binding_hash=? AND expires>?',
                (str(data.get('id','')),digest(binding),time.time())).fetchone()
            if not row:
                raise HTTPException(409, 'Desafio encerrado. Tente novamente.')
            c.execute('DELETE FROM admin_auth_challenges WHERE id=?', (row['id'],))
            credential = data.get('credential', {})
            c.execute('SAVEPOINT verification')
            try:
                if row['purpose'] == 'enroll':
                    if c.execute('SELECT 1 FROM admin_auth_credentials').fetchone() or not c.execute('SELECT 1 FROM admin_auth_pairing WHERE token_hash=? AND expires>?',(row['target'],time.time())).fetchone():
                        raise ValueError()
                    verified = verify_registration_response(credential=credential,
                        expected_challenge=bytes(row['challenge']), expected_rp_id=RP_ID,
                        expected_origin=ORIGIN, require_user_verification=True)
                    if row['expires'] <= time.time():
                        raise ValueError()
                    kid = base64.urlsafe_b64encode(verified.credential_id).rstrip(b'=').decode()
                    c.execute('INSERT INTO admin_auth_credentials VALUES(?,?,?,?,?,?,?)',
                        (kid,verified.credential_public_key,verified.sign_count,time.time(),None,
                         verified.credential_device_type.value,int(verified.credential_backed_up)))
                    c.execute('DELETE FROM admin_auth_pairing')
                    c.execute("DELETE FROM admin_auth_challenges WHERE purpose='enroll'")
                    event(c, 'credential_enrolled')
                else:
                    pending = c.execute("SELECT 1 FROM admin_auth_requests WHERE id=? AND expires>? AND state IN ('pending','scanned')", (row['target'],time.time())).fetchone()
                    key = c.execute('SELECT * FROM admin_auth_credentials WHERE id=?', (credential.get('id',''),)).fetchone()
                    if not key or not pending:
                        raise ValueError()
                    handle = credential.get('response',{}).get('userHandle')
                    if handle is not None and base64url_to_bytes(handle) != USER_ID:
                        raise ValueError()
                    verified = verify_authentication_response(credential=credential,
                        expected_challenge=bytes(row['challenge']), expected_rp_id=RP_ID,
                        expected_origin=ORIGIN, credential_public_key=bytes(key['public_key']),
                        credential_current_sign_count=key['sign_count'],require_user_verification=True)
                    if row['expires'] <= time.time():
                        raise ValueError()
                    c.execute('UPDATE admin_auth_credentials SET sign_count=?,last_used=?,device_type=?,backed_up=? WHERE id=?',
                        (verified.new_sign_count,time.time(),verified.credential_device_type.value,int(verified.credential_backed_up),key['id']))
                    c.execute('UPDATE admin_auth_requests SET state=? WHERE id=?',
                        ('approved' if row['decision']=='approve' else 'rejected',row['target']))
                    c.execute('DELETE FROM admin_auth_challenges WHERE target=?', (row['target'],))
                    event(c, 'qr_approved' if row['decision']=='approve' else 'qr_rejected')
            except Exception:
                # Do not return raw library errors, authenticator data or private paths.
                c.execute('ROLLBACK TO verification')
                invalid = True
                event(c, 'verification_denied')
            c.execute('RELEASE verification')
        if invalid:
            raise HTTPException(401, 'Não foi possível verificar a passkey. Tente novamente.')
        response = JSONResponse(dict(verified=True))
        clear_cookie(response, PHONE)
        return response
