"""Regression tests for deduplicated remote worker control/ready ACKs (#99)."""
import importlib
from unittest.mock import patch

class Response:
    def __init__(self, fail=False):
        self.fail=fail
    def raise_for_status(self):
        if self.fail:
            raise RuntimeError('synthetic network failure')

class Client:
    def __init__(self):
        self.calls=[]
        self.fail=False
    def post(self,route,json):
        self.calls.append((route,dict(json)))
        return Response(self.fail)

def test_control_ack_dedup_and_power_transition(monkeypatch):
    monkeypatch.setenv('STT_WORKER_KEY','test-worker-key')
    with patch('dotenv.load_dotenv',lambda *args,**kwargs:None):
        from server import worker as w
        importlib.reload(w)
    time=[0.]
    client=Client()
    state=w.ControlReporter(client,'notebook',clock=lambda:time[0],refresh_seconds=120)
    assert state.control_payload()=={'id':'notebook'}
    assert state.report(True,True) is True
    assert len(client.calls)==1
    assert client.calls[-1][1]=={'id':'notebook','applied_enabled':True,'ready':True,'reason':None}
    assert state.control_payload()=={'id':'notebook','applied_enabled':True}
    time[0]=20.
    assert state.report(True,True) is False
    assert len(client.calls)==1
    time[0]=121.
    assert state.report(True,True) is True
    assert len(client.calls)==2
    time[0]=130.
    assert state.report(False,False) is True
    assert client.calls[-1][1]['applied_enabled'] is False
    assert state.control_payload()['applied_enabled'] is False
    time[0]=140.
    assert state.report(True,False,'WORKSPACE_UNAVAILABLE') is True
    assert client.calls[-1][1]['ready'] is False
    assert client.calls[-1][1]['reason']=='WORKSPACE_UNAVAILABLE'
    time[0]=160.
    assert state.report(True,False,'OUTBOX_PENDING') is True
    assert client.calls[-1][1]['reason']=='OUTBOX_PENDING'
    time[0]=180.
    assert state.report(True,True) is True
    assert client.calls[-1][1]['reason'] is None

def test_failed_ack_does_not_falsely_advance_reporter(monkeypatch):
    monkeypatch.setenv('STT_WORKER_KEY','test-worker-key')
    with patch('dotenv.load_dotenv',lambda *args,**kwargs:None):
        from server import worker as w
        importlib.reload(w)
    client=Client()
    reporter=w.ControlReporter(client,'notebook',clock=lambda:20.)
    client.fail=True
    try:
        reporter.report(True,True)
        assert False,'Expected failure'
    except RuntimeError:
        pass
    assert reporter.control_payload()=={'id':'notebook'}
    client.fail=False
    assert reporter.report(True,True) is True
    assert reporter.control_payload()['applied_enabled'] is True
