"""Prepare legacy flat exports for a controlled rollback from generations-v1.

Read-only by default. Requires explicitly acknowledged offline/backup state
for any file writes. Never deletes or changes SQLite or active generations.
Only processes unexpired jobs with committed generations-v1 receipts.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

JOB_RE = re.compile(r'[0-9a-f]{32}\Z')
RECEIPT_RE = re.compile(r'[0-9a-f]{64}\Z')


class RollbackError(RuntimeError):
    pass


def _digest(path):
    sha = hashlib.sha256()
    with path.open('rb') as inp:
        for chunk in iter(lambda: inp.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def _regular(path):
    return path.is_file() and not path.is_symlink()


def plan(data, now=None):
    """Prevalidate ALL live committed generations before producing a plan."""
    root = Path(data)
    if root.is_symlink():
        raise RollbackError('DATA_SYMLINK')
    root = root.resolve()
    blob = root / 'blob'
    dbpath = root / 'db' / 'tts.sqlite3'
    if not _regular(dbpath) or not blob.is_dir() or blob.is_symlink():
        raise RollbackError('DATA_LAYOUT_INVALID')
    conn = sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    now = time.time() if now is None else now
    try:
        conn.execute('PRAGMA query_only=ON')
        cols = {r[1] for r in conn.execute('PRAGMA table_info(jobs)')}
        if not {'id','status','expires','receipt_hash','artifact_layout','timing'} <= cols:
            raise RollbackError('UNSUPPORTED_DATABASE_SCHEMA')
        rows = conn.execute("""SELECT id,receipt_hash,timing FROM jobs
           WHERE status='done' AND expires>? AND artifact_layout='generations-v1'
           ORDER BY id""", (now,)).fetchall()
        items = []
        for row in rows:
            jid, receipt, timing = row['id'], row['receipt_hash'], row['timing']
            if not JOB_RE.fullmatch(jid or '') or not RECEIPT_RE.fullmatch(receipt or ''):
                raise RollbackError('INVALID_COMMITTED_REFERENCE')
            if timing not in ('none','segments'):
                raise RollbackError('INVALID_TIMING')
            folder = blob / jid
            source = folder / 'results' / receipt
            if (folder.is_symlink() or (folder/'results').is_symlink() or
                source.is_symlink() or not source.is_dir()):
                raise RollbackError('UNSAFE_RESULT_GENERATION')
            formats = ('txt','md','json') if timing == 'none' else ('txt','md','json','srt')
            actual = {p.name for p in source.iterdir()}
            expected = {'transcript.' + fmt for fmt in formats}
            if actual != expected:
                raise RollbackError('GENERATION_FILES_MISMATCH')
            pending = []
            for fmt in formats:
                src = source / ('transcript.' + fmt)
                dst = folder / ('transcript.' + fmt)
                if not _regular(src) or dst.is_symlink() or (dst.exists() and not dst.is_file()):
                    raise RollbackError('UNSAFE_RESULT_FILE')
                if dst.exists():
                    if _digest(src) != _digest(dst):
                        # A pre-existing flat export might be the only surviving
                        # copy from a previous generation. Never overwrite it.
                        # The operator must resolve the discrepancy offline.
                        raise RollbackError('CONFLICTING_LEGACY_EXPORT')
                else:
                    pending.append((src, dst))
            # A stale legacy SRT must never be visible for an untimed result.
            old_srt = folder / 'transcript.srt'
            if timing == 'none' and (old_srt.is_symlink() or old_srt.exists()):
                raise RollbackError('UNTIMED_LEGACY_SRT_REQUIRES_OFFLINE_REVIEW')
            items.append((jid, pending))
        return items
    finally:
        conn.close()


def _copy_atomic(source, target):
    fd, temporary = tempfile.mkstemp(prefix='.rollback-', suffix='.tmp', dir=target.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as inp:
            shutil.copyfileobj(inp, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        if _digest(source) != _digest(temp_path):
            raise RollbackError('COPY_CHECKSUM_MISMATCH')
        # Fail closed if an existing legacy export appeared since preflight.
        # This also prevents accidental replacement in a wrongly "offline" run.
        if target.exists() or target.is_symlink():
            raise RollbackError('LEGACY_TARGET_CHANGED_DURING_COPY')
        os.replace(temp_path, target)
        if os.name != 'nt':
            directory = os.open(target.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temp_path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--ack-offline-and-backed-up', action='store_true')
    args = parser.parse_args(argv)
    if args.apply != args.ack_offline_and_backed_up:
        parser.error('--apply requires --ack-offline-and-backed-up; do not acknowledge in dry-run')
    try:
        items = plan(args.data_root)
        if args.apply:
            # Revalidate immediately before writing. Operator MUST stop all
            # writers; this script cannot prove a remote worker is offline.
            items = plan(args.data_root)
            for _, updates in items:
                for source, target in updates:
                    _copy_atomic(source, target)
            remaining = plan(args.data_root)
            if sum(len(p) for _, p in remaining):
                raise RollbackError('REMAINS_AFTER_COPY')
        print(json.dumps({'mode':'apply' if args.apply else 'dry-run',
                          'unexpired_generations':len(items),
                          'files_needing_legacy_copy':sum(len(p) for _, p in items),
                          'requires_offline_verified':True,
                          'status':'completed'}))
        return 0
    except (RollbackError, OSError, sqlite3.Error) as exc:
        # No job ID, path, transcript, session key or database content in logs.
        print(json.dumps({'status':'blocked','reason':str(exc) if isinstance(exc,RollbackError)
                          else type(exc).__name__}),file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
