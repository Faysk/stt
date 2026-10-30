"""Issue #69: Whisper can report tail segments past the decoded audio."""
import pytest

from server.segment_timing import reconcile_whisper_segments


def test_clips_just_trailing_endpoints_and_preserves_all_words():
    segments = [dict(start=i, end=i + .8, text=f'word {i}') for i in range(145)]
    segments += [dict(start=145, end=147, text='last one'),
                 dict(start=145.8, end=148, text='last two')]
    result = reconcile_whisper_segments(segments, 146)
    assert result['timing'] == 'segments'
    assert result['result_state'] == 'complete'
    assert len(result['segments']) == 147
    assert [s['text'] for s in result['segments']] == [s['text'] for s in segments]
    assert result['segments'][-2]['end'] == result['segments'][-1]['end'] == 146
    assert result['segments'][-1]['start'] <= result['segments'][-1]['end']
    assert segments[-1]['end'] == 148  # Original timestamps not mutated.


def test_unrepresentable_tail_returns_untimed_transcript_without_losing_text():
    segments = [dict(start=0, end=2, text='Hello'),
                dict(start=11, end=15, text='Preserved suffix')]
    result = reconcile_whisper_segments(segments, 10)
    assert result == dict(segments=[], text='Hello\nPreserved suffix',
                          timing='none', result_state='complete')


def test_silence_and_in_bounds():
    assert reconcile_whisper_segments([], 1.5) == {
        'segments': [], 'timing': 'segments', 'result_state': 'silence'}
    segments = [dict(start=0, end=.4, text='okay')]
    assert reconcile_whisper_segments(segments, 1)['segments'] == segments


def test_bad_duration_rejected():
    for bad in (0, -1, 7201, float('nan'), float('inf'), True):
        with pytest.raises(ValueError):
            reconcile_whisper_segments([], bad)


def test_non_finite_timestamp_does_not_leak_invalid_result():
    result = reconcile_whisper_segments([dict(start=0, end=float('nan'), text='text')], 1)
    assert result['timing'] == 'none'
    assert result['text'] == 'text'


def test_too_many_segments_rejected():
    with pytest.raises(ValueError):
        reconcile_whisper_segments([dict(start=0, end=0, text='')] * 30001, 1)
