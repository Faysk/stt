"""Dry-run, idempotence and fail-closed tests for the offline rollback helper."""
import sqlite3
from pathlib import Path

import pytest

from scripts.restore_legacy_exports import plan, main, RollbackError


def fixture(tmp_path, timing='segments'):
    root = tmp_path / 'private-data'
    (root / 'db').mkdir(parents=True)
    (root / 'blob').mkdir()
    jid, receipt = 'a' * 32, 'b' * 64
    with sqlite3.connect(root / 'db' / 'tts.sqlite3') as db:
        db.execute("""CREATE TABLE jobs
           (id TEXT PRIMARY KEY,status TEXT,expires REAL,
            receipt_hash TEXT,artifact_layout TEXT,timing TEXT)""")
        db.execute("""INSERT INTO jobs VALUES (?,'done',9999999999,?,'generations-v1',?)""",
                   (jid, receipt, timing))
    files = ('txt','md','json') if timing == 'none' else ('txt','md','json','srt')
    target = root / 'blob' / jid / 'results' / receipt
    target.mkdir(parents=True)
    for fmt in files:
        (target / ('transcript.' + fmt)).write_text('fixture ' + fmt)
    return root, jid, target


def test_dry_run_keeps_files_untouched_and_apply_is_idempotent(tmp_path, capsys):
    root, jid, generation = fixture(tmp_path)
    flat = root / 'blob' / jid / 'transcript.txt'
    assert len(plan(root)) == 1
    assert main(['--data-root', str(root)]) == 0
    assert not flat.exists()
    assert main(['--data-root', str(root),'--apply','--ack-offline-and-backed-up']) == 0
    assert flat.read_text() == 'fixture txt'
    assert len(list((root/'blob'/jid).glob('transcript.*'))) == 4
    assert main(['--data-root',str(root),'--apply','--ack-offline-and-backed-up']) == 0
    assert flat.read_text() == 'fixture txt'
    assert (generation/'transcript.txt').read_text() == 'fixture txt'
    assert 'completed' in capsys.readouterr().out


def test_apply_requires_explicit_offline_backup_acknowledgement(tmp_path):
    root, _, _ = fixture(tmp_path)
    with pytest.raises(SystemExit):
        main(['--data-root',str(root),'--apply'])
    with pytest.raises(SystemExit):
        main(['--data-root',str(root),'--ack-offline-and-backed-up'])


def test_preflight_checks_all_sources_before_writing_any(tmp_path, capsys):
    root, jid, generation = fixture(tmp_path)
    (generation / 'transcript.md').unlink()
    assert main(['--data-root',str(root),'--apply','--ack-offline-and-backed-up']) == 2
    assert not (root/'blob'/jid/'transcript.txt').exists()
    assert 'GENERATION_FILES_MISMATCH' in capsys.readouterr().err


def test_untimed_export_cannot_resurrect_stale_subtitles(tmp_path):
    root, jid, generation = fixture(tmp_path, 'none')
    assert main(['--data-root',str(root),'--apply','--ack-offline-and-backed-up']) == 0
    assert not (root/'blob'/jid/'transcript.srt').exists()
    (root/'blob'/jid/'transcript.srt').write_text('stale subtitle')
    with pytest.raises(RollbackError, match='UNTIMED_LEGACY_SRT'):
        plan(root)


def test_symlinked_generation_is_never_followed(tmp_path):
    root, jid, generation = fixture(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    from shutil import rmtree
    rmtree(generation)
    generation.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RollbackError, match='UNSAFE_RESULT_GENERATION'):
        plan(root)


def test_conflicting_flat_result_is_never_overwritten(tmp_path,capsys):
    root,jid,_=fixture(tmp_path)
    target=root/'blob'/jid/'transcript.txt'
    target.write_text('existing unique transcript',encoding='utf-8')
    assert main(['--data-root',str(root),'--apply','--ack-offline-and-backed-up'])==2
    assert target.read_text(encoding='utf-8')=='existing unique transcript'
    assert not (root/'blob'/jid/'transcript.md').exists()
    assert 'CONFLICTING_LEGACY_EXPORT' in capsys.readouterr().err


def test_target_created_after_preflight_is_not_replaced(tmp_path,monkeypatch):
    import shutil
    from scripts import restore_legacy_exports as helper

    source=tmp_path/'committed.txt'
    target=tmp_path/'legacy.txt'
    source.write_text('new-generation-content',encoding='utf-8')
    original=shutil.copyfileobj
    def inject_race(inp,out,*args,**kwargs):
        original(inp,out,*args,**kwargs)
        target.write_text('existing created during copy',encoding='utf-8')
    monkeypatch.setattr(helper.shutil,'copyfileobj',inject_race)
    with pytest.raises(RollbackError,match='LEGACY_TARGET_CHANGED_DURING_COPY'):
        helper._copy_atomic(source,target)
    assert target.read_text(encoding='utf-8')=='existing created during copy'
    assert not list(tmp_path.glob('.rollback-*.tmp'))
