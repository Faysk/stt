"""Reconcile ASR timestamps against the decoded audio duration.

Whisper may emit segment endpoints beyond the last decoded sample. Never
invent audio duration to accommodate such endpoints. Clip a late endpoint to
the decoded boundary when its start is still in bounds; if timestamps cannot
be represented safely, preserve *all* words as untimed text instead.
"""
import math

MAX_AUDIO_SECONDS = 7200
MAX_SEGMENTS = 30000


def reconcile_whisper_segments(segments, decoded_duration):
    """Return API-compatible timing fields without discarding transcript text.

    ``decoded_duration`` is measured from decoded frames (not a container hint
    or the model's possibly padded duration). No segment may extend past it.
    """
    if (isinstance(decoded_duration, bool) or
            not isinstance(decoded_duration, (int, float)) or
            not math.isfinite(decoded_duration) or
            not 0 < decoded_duration <= MAX_AUDIO_SECONDS):
        raise ValueError('Invalid decoded audio duration')
    if len(segments) > MAX_SEGMENTS:
        raise ValueError('Too many segments')

    corrected = []
    invalid_timing = False
    previous_start = -1.0
    for item in segments:
        start, end = item['start'], item['end']
        if (any(isinstance(value, bool) or
                not isinstance(value, (int, float)) or
                not math.isfinite(value) for value in (start, end)) or
                start < 0 or end < start):
            invalid_timing = True
        if not invalid_timing:
            if start + 1e-6 < previous_start or start > decoded_duration:
                invalid_timing = True
            else:
                corrected.append(dict(item, end=min(end, decoded_duration)))
        previous_start = start

    if invalid_timing:
        # A segment starting beyond the physical audio cannot be assigned a
        # truthful timestamp. Keep its words, but don't publish bogus SRT.
        text = '\n'.join(item['text'].strip() for item in segments)
        return dict(segments=[], text=text, timing='none',
                    result_state='complete' if text.strip() else 'silence')
    return dict(segments=corrected, timing='segments',
                result_state='complete' if corrected else 'silence')
