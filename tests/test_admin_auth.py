"""Real ES256/WebAuthn ceremonies against isolated SQLite; no GPU or private keys from users."""
import base64
import hashlib
import importlib
import json
import secrets
import struct
import time

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from server import admin_auth as auth

HEADERS = {'origin': auth.ORIGIN, 'x-stt-auth': '1'}


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


class Authenticator:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = secrets.token_bytes(32)
        self.counter = 0

    def client_data(self, options, kind, origin=auth.ORIGIN):
        return json.dumps(dict(type=kind, challenge=options['publicKey']['challenge'], origin=origin, crossOrigin=False), separators=(',', ':')).encode()

    def register(self, options, uv=True, origin=auth.ORIGIN):
        public = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1:2,3:-7,-1:1,-2:public.x.to_bytes(32,'big'),-3:public.y.to_bytes(32,'big')})
        data = hashlib.sha256(auth.RP_ID.encode()).digest()+bytes([0x45 if uv else 0x41])+struct.pack('>I',0)+bytes(16)+struct.pack('>H',len(self.id))+self.id+cose
        attestation = cbor2.dumps(dict(fmt='none',attStmt={},authData=data))
        return dict(id=b64(self.id),rawId=b64(self.id),type='public-key',response={
            'clientDataJSON':b64(self.client_data(options,'webauthn.create',origin)),
            'attestationObject':b64(attestation),'transports':['internal']})

    def assertion(self, options, uv=True, origin=auth.ORIGIN, rp=auth.RP_ID, handle=auth.USER_ID):
        self.counter += 1
        data = hashlib.sha256(rp.encode()).digest()+bytes([5 if uv else 1])+struct.pack('>I',self.counter)
        client = self.client_data(options,'webauthn.get',origin)
        signature = self.key.sign(data+hashlib.sha256(client).digest(),ec.ECDSA(hashes.SHA256()))
        return dict(id=b64(self.id),rawId=b64(self.id),type='public-key',response={
            'clientDataJSON':b64(client),'authenticatorData':b64(data),
            'signature':b64(signature),'userHandle':b64(handle)})


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('STT_DATA',str(tmp_path))
    monkeypatch.setenv('STT_REQUIRE_EDGE','0')
    monkeypatch.setenv('STT_ADMIN_QR_ENABLED','1')
    monkeypatch.setenv('STT_ADMIN_LEGACY_ENABLED','0')
    monkeypatch.setenv('STT_ADMIN_KEY','test-recovery-key')
    import server.app as module
    m = importlib.reload(module)
    m.init()
    desktop = TestClient(m.app,base_url=auth.ORIGIN,headers=HEADERS)
    phone = TestClient(m.app,base_url=auth.ORIGIN,headers=HEADERS)
    yield desktop,phone,m
    desktop.close();phone.close()


def options(client, **body):
    r = client.post('/api/admin/auth/ceremony/options',json=body)
    assert r.status_code == 200, r.text
    return r.json()


def finish(client, opts, credential):
    return client.post('/api/admin/auth/ceremony/finish',json=dict(id=opts['id'],credential=credential),headers={'x-csrf-token':opts['csrf']})


def enroll(phone,m):
    token = auth.new_pairing(m.db).split('#pair=')[1]
    device = Authenticator()
    opts = options(phone,purpose='enroll',pair=token)
    response = finish(phone,opts,device.register(opts))
    assert response.status_code == 200, response.text
    assert auth.SESSION not in phone.cookies
    return device


def start(desktop):
    response = desktop.post('/api/admin/auth/qr/start',json={})
    assert response.status_code == 200,response.text
    for flag in ('Secure','HttpOnly','SameSite=strict','Path=/'):
        assert flag in response.headers['set-cookie']
    data = response.json()
    assert data['url'] == auth.ORIGIN+'/admin-approve.html?request='+data['request']
    assert len(data['request']) == 64
    assert 'test-recovery-key' not in response.text and desktop.cookies[auth.DESKTOP] not in response.text
    assert '<svg' in data['svg']
    return data


def approve(phone, device, qr, **kwargs):
    opts = options(phone,purpose='approve',request=qr['request'],decision=kwargs.pop('decision','approve'))
    return finish(phone,opts,device.assertion(opts,**kwargs))


def consume(desktop,qr):
    return desktop.post('/api/admin/auth/qr/consume',json={'request':qr['request']},headers={'x-csrf-token':qr['csrf']})


def test_complete_real_crypto_session_csrf_logout_and_original_browser(setup):
    pc,phone,m = setup
    device = enroll(phone,m)
    qr = start(pc)
    assert pc.get('/api/admin').status_code == 401
    assert phone.post('/api/admin/auth/approve/context',json={'request':qr['request']}).json()['code'] == qr['code']
    assert approve(phone,device,qr).status_code == 200
    assert auth.SESSION not in phone.cookies
    # Photographing the QR and even knowing the returned CSRF is insufficient.
    assert consume(phone,qr).status_code in (401,403)
    session = consume(pc,qr)
    assert session.status_code == 200,session.text
    assert auth.SESSION in pc.cookies
    assert 'domain=' not in session.headers['set-cookie'].lower()
    assert pc.cookies[auth.SESSION] not in session.text
    assert consume(pc,qr).status_code in (401,403,409)
    assert pc.get('/api/admin').status_code == 200
    settings = dict(worker='notebook',model='tiny',device='cpu',compute='int8',paused=False)
    assert pc.put('/api/admin/settings',json=settings).status_code == 403
    secured = {'x-csrf-token':session.json()['csrf']}
    assert pc.put('/api/admin/settings',json=settings,headers=secured).status_code == 200
    assert pc.put('/api/admin/settings',json=settings,headers={**secured,'origin':'https://evil.example'}).status_code == 403
    saved = pc.cookies[auth.SESSION]
    assert pc.post('/api/admin/auth/logout',json={},headers=secured).status_code == 200
    assert pc.get('/api/admin').status_code == 401
    pc.cookies.set(auth.SESSION,saved,domain=auth.RP_ID,path='/')
    assert pc.get('/api/admin').status_code == 401
    with m.db() as c:
        assert c.execute('SELECT count(*) FROM admin_auth_sessions').fetchone()[0] == 0


@pytest.mark.parametrize('bad', ['origin','rp','uv','handle','signature','challenge','unknown'])
def test_webauthn_verifier_denies_invalid_assertions_and_consumes_challenge(setup,bad):
    pc,phone,m = setup
    device = enroll(phone,m); qr = start(pc)
    opts = options(phone,purpose='approve',request=qr['request'])
    kwargs = {'origin':'https://evil.example'} if bad=='origin' else {'rp':'evil.example'} if bad=='rp' else {'uv':False} if bad=='uv' else {'handle':b'other-owner'} if bad=='handle' else {}
    if bad=='challenge': opts['publicKey']['challenge'] = b64(b'wrong')
    assertion = device.assertion(opts,**kwargs)
    if bad=='signature': assertion['response']['signature'] = b64(b'not-a-signature')
    if bad=='unknown': assertion['id'] = b64(b'not-registered')
    response = finish(phone,opts,assertion)
    assert response.status_code == 401,response.text
    assert finish(phone,opts,assertion).status_code == 409
    assert consume(pc,qr).status_code == 409
    with m.db() as c:
        assert c.execute('SELECT count(*) FROM admin_auth_sessions').fetchone()[0] == 0
        assert c.execute('SELECT sign_count FROM admin_auth_credentials').fetchone()[0] == 0


def test_expiry_cancel_reject_and_recovery(setup):
    pc,phone,m = setup
    device = enroll(phone,m); qr = start(pc)
    assert approve(phone,device,qr,decision='reject').status_code == 200
    assert consume(pc,qr).status_code == 409
    qr = start(pc)
    assert pc.post('/api/admin/auth/qr/cancel',json={'request':qr['request']},headers={'x-csrf-token':qr['csrf']}).status_code == 200
    assert phone.post('/api/admin/auth/approve/context',json={'request':qr['request']}).status_code == 404
    qr = start(pc)
    opts = options(phone,purpose='approve',request=qr['request'])
    with m.db() as c: c.execute('UPDATE admin_auth_requests SET expires=?',(time.time()-1,))
    assert finish(phone,opts,device.assertion(opts)).status_code == 401
    assert consume(pc,qr).status_code in (401,409)
    qr = start(pc)
    assert approve(phone,device,qr).status_code == 200
    assert consume(pc,qr).status_code == 200
    with pytest.raises(ValueError): auth.new_pairing(m.db)
    auth.new_pairing(m.db,recover=True)
    assert pc.get('/api/admin').status_code == 401
    with m.db() as c:
        assert c.execute('SELECT count(*) FROM admin_auth_requests').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM admin_auth_credentials').fetchone()[0] == 0


def test_enrollment_is_local_authorized_single_use_and_requires_uv(setup):
    pc,phone,m = setup
    assert phone.post('/api/admin/auth/ceremony/options',json={'purpose':'enroll','pair':'guessed'}).status_code == 403
    token = auth.new_pairing(m.db).split('#pair=')[1]
    device = Authenticator(); opts = options(phone,purpose='enroll',pair=token)
    assert finish(phone,opts,device.register(opts,uv=False)).status_code == 401
    opts = options(phone,purpose='enroll',pair=token)
    assert finish(pc,opts,device.register(opts)).status_code == 403
    assert finish(phone,opts,device.register(opts)).status_code == 200
    assert finish(phone,opts,device.register(opts)).status_code in (403,409)
    assert phone.post('/api/admin/auth/ceremony/options',json={'purpose':'enroll','pair':token}).status_code == 403


def test_idle_absolute_expiration_and_header_fallback_flag(setup,monkeypatch):
    pc,phone,m = setup
    device=enroll(phone,m); qr=start(pc); approve(phone,device,qr); consume(pc,qr)
    with m.db() as c: c.execute('UPDATE admin_auth_sessions SET seen=?',(time.time()-auth.IDLE_TTL-1,))
    assert pc.get('/api/admin').status_code == 401
    assert pc.get('/api/admin',headers={'x-admin-key':'test-recovery-key'}).status_code == 401
    monkeypatch.setenv('STT_ADMIN_LEGACY_ENABLED','1')
    assert pc.get('/api/admin',headers={'x-admin-key':'test-recovery-key'}).status_code == 200
    monkeypatch.setenv('STT_ADMIN_QR_ENABLED','0')
    assert pc.post('/api/admin/auth/qr/start',json={}).status_code == 404


def test_cross_site_body_limits_rate_limits_and_privacy_headers(setup):
    pc,phone,m = setup
    assert pc.post('/api/admin/auth/qr/start',json={},headers={'origin':'https://evil.example'}).status_code == 403
    assert pc.post('/api/admin/auth/qr/start',json={},headers={'sec-fetch-site':'cross-site'}).status_code == 403
    assert pc.post('/api/admin/auth/qr/start',content='x'*32769,headers={'content-type':'application/json'}).status_code == 413
    response=pc.post('/api/admin/auth/qr/start',json={})
    for key,value in [('cache-control','no-store'),('x-robots-tag','noindex, nofollow, noarchive')]:
        assert response.headers[key]==value
    # Ignore caller-supplied edge identity when edge guard is disabled.
    for n in range(12):
        response=pc.post('/api/admin/auth/qr/start',json={},headers={'x-stt-client':str(n)})
        if response.status_code==429: break
    assert response.status_code==429
    with m.db() as c:
        assert c.execute("SELECT count(*) FROM admin_auth_requests WHERE state='pending'").fetchone()[0] == 1
        event_text=json.dumps([dict(r) for r in c.execute('SELECT * FROM admin_auth_events')])
        assert 'test-recovery-key' not in event_text and auth.ORIGIN not in event_text


def test_schema_migration_preserves_jobs_and_cleanup_is_bounded(setup):
    pc,phone,m=setup
    with m.db() as c:
        c.execute("INSERT INTO settings VALUES(99,'existing')")
        auth.install_schema(c);auth.install_schema(c)
        assert c.execute('SELECT value FROM settings WHERE id=99').fetchone()[0]=='existing'
        c.execute("INSERT INTO admin_auth_events(at,event) VALUES(0,'old')")
        auth.cleanup(c)
        assert c.execute('SELECT count(*) FROM admin_auth_events').fetchone()[0]==0


def test_replay_counter_and_cross_request_challenge_binding(setup):
    pc,phone,m=setup
    device=enroll(phone,m);qr=start(pc)
    opts=options(phone,purpose='approve',request=qr['request'])
    first=device.assertion(opts)
    assert finish(phone,opts,first).status_code==200
    qr=start(pc);opts=options(phone,purpose='approve',request=qr['request'])
    # A valid prior signature with a different request/challenge must fail.
    assert finish(phone,opts,first).status_code==401
    opts=options(phone,purpose='approve',request=qr['request'])
    device.counter=0
    assert finish(phone,opts,device.assertion(opts)).status_code==401


def test_approved_request_expiry_never_creates_session(setup):
    pc,phone,m=setup
    device=enroll(phone,m);qr=start(pc);approve(phone,device,qr)
    with m.db() as c: c.execute('UPDATE admin_auth_requests SET expires=?',(time.time()-1,))
    assert consume(pc,qr).status_code==409


def test_two_browser_consumers_race_only_one_session_is_created(setup):
    from concurrent.futures import ThreadPoolExecutor
    pc,phone,m=setup
    device=enroll(phone,m);qr=start(pc);approve(phone,device,qr)
    cookie=pc.cookies[auth.DESKTOP]
    def attempt(_):
        other=TestClient(m.app,base_url=auth.ORIGIN,headers=HEADERS)
        try:
            other.cookies.set(auth.DESKTOP,cookie,domain=auth.RP_ID,path='/')
            return consume(other,qr).status_code
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,range(2)))
    assert sorted(results)==[200,409]
    with m.db() as c:
        assert c.execute('SELECT count(*) FROM admin_auth_sessions').fetchone()[0]==1


def test_origin_guard_still_protects_auth_endpoints(setup,monkeypatch):
    pc,phone,m=setup
    monkeypatch.setenv('STT_REQUIRE_EDGE','1')
    monkeypatch.setenv('STT_EDGE_KEY','edge-test-only')
    assert pc.get('/api/admin/auth/config').status_code==403
    assert pc.get('/api/admin/auth/config',headers={'x-edge-key':'edge-test-only'}).status_code==200


def test_approval_cannot_be_replayed_for_replacement_qr(setup):
    pc,phone,m=setup
    device=enroll(phone,m);old=start(pc)
    opts=options(phone,purpose='approve',request=old['request'])
    current=start(pc)
    assert consume(pc,old).status_code==401
    assert finish(phone,opts,device.assertion(opts)).status_code==401
    assert consume(pc,current).status_code==409
