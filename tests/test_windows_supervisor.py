"""Restart policy must not confuse a shared Python runtime with this project."""
import importlib.util
from pathlib import Path
import psutil
import os
import pytest

spec = importlib.util.spec_from_file_location('stt_supervisor', Path(__file__).parents[1]/'scripts/supervise.py')
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class Process:
    def __init__(self, executable, command, alive=True):
        self.executable, self.command, self.alive = executable, command, alive

    def exe(self): return self.executable
    def cmdline(self): return self.command
    def is_running(self): return self.alive


def test_only_project_venv_and_exact_module_are_adopted():
    assert supervisor.matches(Process(str(supervisor.RUNTIME), ['python', '-m', 'server.worker']), 'worker')
    assert not supervisor.matches(Process('C:/other/python.exe', ['python', '-m', 'server.worker']), 'worker')
    assert not supervisor.matches(Process(str(supervisor.RUNTIME), ['python', '-m', 'server.tunnel']), 'worker')
    assert not supervisor.matches(Process(str(supervisor.RUNTIME), ['python', '-m', 'server.worker'], False), 'worker')


def test_access_denied_is_never_treated_as_verified_process():
    class Unreadable:
        def exe(self): raise psutil.AccessDenied()
    assert not supervisor.matches(Unreadable(), 'worker')


def test_worker_only_mode_never_starts_an_independent_api_or_tunnel():
    assert supervisor.desired_roles({'STT_HOST_API': '0', 'STT_TUNNEL_NAME': 'faysk-stt'}) == ['worker']
    assert supervisor.desired_roles({'STT_TUNNEL_NAME': 'faysk-stt'}) == ['api', 'worker', 'tunnel']
    assert supervisor.desired_roles({}) == ['api', 'worker']


@pytest.mark.skipif(os.name!='nt',reason='Windows byte-range lock')
def test_second_supervisor_exits_cleanly_without_reading_locked_byte(tmp_path):
    path=tmp_path/'supervisor.lock'
    first=supervisor.lock_instance(path)
    assert first is not None
    try:
        assert supervisor.lock_instance(path) is None
    finally:
        first.close()
    again=supervisor.lock_instance(path)
    assert again is not None
    again.close()
