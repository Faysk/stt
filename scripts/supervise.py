"""Current-user Windows supervisor. Adopt verified STT processes; never kill them."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import psutil
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / 'logs'
RUNTIME = ROOT / '.venv/Scripts/python.exe'
MODULES = {'api': 'server.app:app', 'worker': 'server.worker', 'tunnel': 'server.tunnel'}


def lock_instance(path):
    import msvcrt
    stream = path.open('a+b')
    # Reading a byte already locked by another Windows process itself raises
    # PermissionError. Check size without reading before attempting the lock.
    if path.stat().st_size == 0:
        stream.write(b'0')
        stream.flush()
    stream.seek(0)
    try:
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        stream.close()
        return None
    return stream


def matches(process, role):
    """The venv launcher is project-specific, unlike the shared base Python."""
    try:
        return (os.path.normcase(process.exe()) == os.path.normcase(str(RUNTIME))
                and MODULES[role] in process.cmdline()
                and process.is_running())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def port_busy():
    with socket.socket() as connection:
        connection.settimeout(1)
        return connection.connect_ex(('127.0.0.1', 8765)) == 0


def find_existing(role):
    for process in psutil.process_iter():
        if matches(process, role):
            return process
    return None


def desired_roles(settings):
    if settings.get('STT_HOST_API', '1') == '0':
        return ['worker']
    return ['api', 'worker'] + (['tunnel'] if settings.get('STT_TUNNEL_NAME') or settings.get('STT_TUNNEL_TOKEN') else [])


def supervise(once=False):
    LOGS.mkdir(exist_ok=True)
    guard = lock_instance(LOGS / 'supervisor.lock')
    if guard is None:
        return
    logger = logging.getLogger('stt-supervisor')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOGS / 'supervisor.log', maxBytes=256*1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    known = {}
    retry_after = {}
    blocked = set()
    while True:
        settings = dotenv_values(ROOT / '.env')
        for role in desired_roles(settings):
            old = known.get(role)
            if old is not None and matches(old, role):
                continue
            existing = find_existing(role)
            if existing is not None:
                known[role] = existing
                logger.info('Adopted %s pid=%s', role, existing.pid)
                continue
            if role == 'api' and port_busy():
                if role not in blocked:
                    logger.error('API port occupied; no duplicate started')
                    blocked.add(role)
                continue
            if time.monotonic() < retry_after.get(role, 0):
                continue
            blocked.discard(role)
            arguments = (['-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', '8765', '--no-access-log']
                         if role == 'api' else ['-m', MODULES[role]])
            # Bound logs before launching; never rotate handles of adopted processes.
            for suffix in ('log', 'error.log'):
                path = LOGS / ('supervised-' + role + '.' + suffix)
                if path.exists() and path.stat().st_size > 2*1024**2:
                    path.replace(path.with_suffix(path.suffix + '.previous'))
            with (LOGS / ('supervised-' + role + '.log')).open('ab') as output, (LOGS / ('supervised-' + role + '.error.log')).open('ab') as errors:
                try:
                    child = subprocess.Popen([str(RUNTIME), *arguments], cwd=ROOT, stdin=subprocess.DEVNULL,
                                             stdout=output, stderr=errors, creationflags=subprocess.CREATE_NO_WINDOW)
                    known[role] = psutil.Process(child.pid)
                    retry_after[role] = time.monotonic() + 30
                    logger.info('Started %s pid=%s', role, child.pid)
                    ids = {name: process.pid for name, process in known.items() if name in ('api', 'worker')}
                    previous = LOGS / 'pids.json'
                    if previous.exists():
                        ids = {**json.loads(previous.read_text(encoding='utf-8-sig')), **ids}
                    previous.write_text(json.dumps(ids), encoding='utf-8')
                    if role == 'tunnel':
                        (LOGS / 'tunnel.pid').write_text(str(child.pid))
                except OSError:
                    logger.error('Failed to start %s', role)
                    retry_after[role] = time.monotonic() + 30
        if once:
            return
        time.sleep(10)


def install():
    import winreg
    runtime = RUNTIME.with_name('pythonw.exe')
    if not runtime.is_file():
        raise SystemExit('Missing project pythonw runtime')
    command = subprocess.list2cmdline([str(runtime), str(Path(__file__).resolve())])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        try:
            existing, _ = winreg.QueryValueEx(key, 'FayskSTT')
        except FileNotFoundError:
            existing = command
        if existing != command:
            raise SystemExit('Existing startup entry differs; refusing overwrite')
        winreg.SetValueEx(key, 'FayskSTT', 0, winreg.REG_SZ, command)
    subprocess.Popen([str(runtime), str(Path(__file__).resolve())], cwd=ROOT,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    print('STT current-user startup installed; starts after Windows sign-in. No services stopped.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('Windows only')
    if args.install:
        install()
    else:
        supervise(args.once)
