"""Safe polling defaults for remote and S7 STT workers (#99)."""
import importlib
from unittest.mock import patch


def test_s7_polling_backoff_and_limits(monkeypatch):
    from s7 import worker as s7
    assert s7.SLEEP_SECONDS >= 20
    assert 8 <= s7.retry_seconds(1) <= 13
    assert 100 <= s7.retry_seconds(10) <= 140
    monkeypatch.setenv("STT_S7_IDLE_SECONDS","0")
    assert s7.idle_seconds()==5
    monkeypatch.setenv("STT_S7_IDLE_SECONDS","999")
    assert s7.idle_seconds()==60
    monkeypatch.setenv("STT_S7_IDLE_SECONDS","nan")
    assert s7.idle_seconds()==20


def test_remote_worker_idle_polling_and_bounded_retries(monkeypatch):
    with patch("dotenv.load_dotenv",lambda *args,**kwargs:None):
        from server import worker as win
        importlib.reload(win)
    monkeypatch.setenv("STT_WORKER_IDLE_SECONDS","20")
    assert win.poll_interval("STT_WORKER_IDLE_SECONDS",20)==20
    monkeypatch.setenv("STT_WORKER_IDLE_SECONDS","0")
    assert win.poll_interval("STT_WORKER_IDLE_SECONDS",20)==5
    monkeypatch.setenv("STT_WORKER_IDLE_SECONDS","500")
    assert win.poll_interval("STT_WORKER_IDLE_SECONDS",20)==60
    monkeypatch.setenv("STT_WORKER_IDLE_SECONDS","nan")
    assert win.poll_interval("STT_WORKER_IDLE_SECONDS",20)==20
    assert 8 <= win.failure_delay(1) <= 23
    assert 100 <= win.failure_delay(10) <= 140
