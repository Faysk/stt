"""Release safety regression tests: credentials and security headers.

All network calls are fake. No production jobs or secrets are used.
"""
import importlib
import json
import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient


def worker(tmp_path, monkeypatch):
    monkeypatch.setenv('TTS_WORKER_KEY','dummy-worker')
    monkeypatch.setenv('TTS_OUTBOX',str(tmp_path/'outbox'))
    monkeypatch.delenv('STT_WORKER_KEY',raising=False)
    monkeypatch.delenv('STT_OUTBOX',raising=False)
    with patch('dotenv.load_dotenv', lambda *args,**kwargs: None):
        import server.worker as module
        return importlib.reload(module)


def reply(code):
    return httpx.Response(code,json={'detail':'synthetic'},request=httpx.Request('POST','https://invalid.example/result'))


@pytest.mark.parametrize('status_code',[401,403])
def test_auth_denial_pauses_new_claims_without_dead_letter(tmp_path,monkeypatch,status_code):
    w=worker(tmp_path,monkeypatch)
    path=w._save_outbox({'id':'auth-test','lease':'synthetic','expires':time.time()+600},
                       {'text':'private test result'})
    calls=[]

    class Client:
        def post(self,*args,**kwargs):
            calls.append(1)
            return reply(status_code)

    monkeypatch.setattr(w,'client',Client())
    assert w._drain_outbox() is True
    data=json.loads(path.read_text(encoding='utf-8'))
    assert data['last_status']==status_code
    assert data['retry_at']>time.time()
    assert not data.get('dead_letter')
    assert data['payload']['text']=='private test result'
    assert w._drain_outbox() is True
    assert len(calls)==1


def test_auth_recovers_after_operator_corrects_credentials(tmp_path,monkeypatch):
    w=worker(tmp_path,monkeypatch)
    job={'id':'recover-auth','lease':'synthetic','expires':time.time()+600}
    path=w._save_outbox(job,{'text':'preserved'})
    attempts=[]

    class Client:
        def post(self,*args,**kwargs):
            attempts.append(1)
            if len(attempts)==1:
                return reply(403)
            return httpx.Response(200,json={'ok':True,'receipt':'c'*64},
                                  request=httpx.Request('POST','https://invalid.example/result'))

    monkeypatch.setattr(w,'client',Client())
    now=time.time()
    assert w._deliver(path,now)=='auth_blocked'
    assert path.exists()
    assert w._deliver(path,now+10)=='acked'
    assert len(attempts)==2
    assert not path.exists()


def test_permanent_unprocessable_payload_still_quarantines(tmp_path,monkeypatch):
    w=worker(tmp_path,monkeypatch)
    path=w._save_outbox({'id':'validation-error','lease':'synthetic',
                         'expires':time.time()+600},{'text':'bad payload'})

    class Client:
        def post(self,*args,**kwargs):
            return reply(422)

    monkeypatch.setattr(w,'client',Client())
    assert w._deliver(path)=='quarantined'
    data=json.loads(path.read_text())
    assert data['dead_letter_status']==422
    assert data['payload']['text']=='bad payload'
