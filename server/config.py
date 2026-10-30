"""STT-only environment access; no legacy configuration aliases."""
import os


def getenv(name, default=None):
    if not name.startswith('STT_'):
        raise ValueError('Expected STT_ configuration key')
    return os.environ.get(name, default)


def required(name):
    value = getenv(name)
    if not value:
        raise RuntimeError('Missing STT configuration: ' + name)
    return value
