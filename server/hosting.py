"""Admission capabilities for a small, always-on coordinator."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from fastapi import HTTPException
from server.config import getenv

SMALL_FORMATS = ('wav', 'mp3', 'ogg', 'm4a')


def capabilities(connection, now=None):
    now = time.time() if now is None else now
    settings = json.loads(connection.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
    primary = connection.execute('SELECT seen FROM workers WHERE id=?', (settings['worker'],)).fetchone()
    control=connection.execute('SELECT enabled FROM worker_controls WHERE id=?',(settings['worker'],)).fetchone()
    restricted = getenv('STT_LIGHT_HOST', '0') == '1' and (
        settings['worker'] == 's7-edge' or primary is None or now-primary['seen'] >= 90 or control is not None and not control['enabled'])
    return dict(limited=restricted, max_mb=8 if restricted else 90,
                max_seconds=300 if restricted else 7200,
                formats=list(SMALL_FORMATS) if restricted else None,
                youtube=not restricted)


def storage_guard(blob, reserve=0):
    """SD data must never silently fall back to the internal Android volume."""
    if getenv('STT_LIGHT_HOST', '0') != '1':
        return
    home = Path.home()
    if not blob.is_dir() or blob.stat().st_dev == home.stat().st_dev:
        raise HTTPException(503, 'Armazenamento temporariamente indisponível.')
    if shutil.disk_usage(home).free < 2*1024**3:
        raise HTTPException(503, 'Armazenamento temporariamente indisponível.')
    budget = int(getenv('STT_BLOB_BUDGET_MB', '2048')) * 1024**2
    used = 0
    for root, directories, files in os.walk(blob, followlinks=False):
        for name in directories + files:
            path = Path(root)/name
            if path.is_symlink():
                raise HTTPException(503, 'Armazenamento temporariamente indisponível.')
        for name in files:
            try:
                used += (Path(root)/name).stat().st_size
            except FileNotFoundError:
                pass
    if used+reserve > budget or shutil.disk_usage(blob).free < reserve+256*1024**2:
        raise HTTPException(503, 'Armazenamento temporariamente indisponível.')


def duration_probe(path, media_type):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                 '-f', ('mov' if media_type=='m4a' else media_type), '-show_entries', 'format=duration',
                                 '-of', 'json', str(path)], capture_output=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        value = float(json.loads(result.stdout)['format']['duration'])
        if result.returncode or not 0 < value < 86400:
            raise ValueError()
        return value
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        raise HTTPException(400, 'Não foi possível validar a duração deste áudio.')
