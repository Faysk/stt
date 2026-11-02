"""Bounded, worker-owned temporary directories; fail fast on Windows ACL errors."""
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import shutil


MIN_FREE = 512 * 1024**2


def root_path(value):
    root = Path(value).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise OSError('Unsafe worker workspace')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not root.is_dir():
        raise OSError('Worker workspace is not a directory')
    return root


@contextmanager
def directory(root,before_cleanup=None):
    root = root_path(root)
    # tempfile.mkdtemp retries some Windows PermissionErrors as collisions.
    # Only an actual existing name is a reason to try a different name here.
    for _ in range(8):
        folder = root / ('stt-' + secrets.token_hex(12))
        try:
            folder.mkdir(mode=0o700)
            break
        except FileExistsError:
            continue
    else:
        raise OSError('Worker workspace name collision budget exhausted')
    try:
        yield folder
    finally:
        if before_cleanup is not None:before_cleanup()
        shutil.rmtree(folder)


def preflight(root, outbox):
    root = root_path(root)
    outbox = root_path(outbox)
    for target in {root, outbox}:
        if shutil.disk_usage(target).free < MIN_FREE:
            raise OSError('Worker storage reserve unavailable')
        # Verify actual write/flush/delete permissions, not os.access alone.
        with directory(target) as probe:
            with (probe/'probe').open('xb') as stream:
                stream.write(b'stt')
                stream.flush()
                os.fsync(stream.fileno())
    return root
