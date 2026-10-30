"""Compatibility bridge for the TTS -> STT naming migration.

Existing deployments can keep TTS_* until cutover. New deployments may use
STT_*. Conflicting values fail closed with names only, never secret values.
Do not remove this bridge until a tested rollback/legacy-host policy exists.
"""
import os


def getenv(legacy, default=None):
    if not legacy.startswith('TTS_'):
        raise ValueError('Expected a legacy TTS_ configuration key')
    modern = 'STT_' + legacy[4:]
    new = os.environ.get(modern)
    old = os.environ.get(legacy)
    if new is not None and old is not None and new != old:
        # Names only: credentials and token values MUST NEVER enter logs.
        raise RuntimeError('Conflicting STT/TTS configuration: ' + legacy[4:])
    if new is not None:
        return new
    if old is not None:
        return old
    return default


def required(legacy):
    result = getenv(legacy)
    if result is None or result == '':
        raise RuntimeError('Missing STT/TTS configuration: ' + legacy[4:])
    return result
