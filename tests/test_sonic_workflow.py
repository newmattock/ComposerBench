import json
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from composerbench.adapter.base import RenderRequest
from composerbench.analysis.audition import compare_audio, describe, excerpt, inspect_audio
from composerbench.analysis.wav import read_wav, write_float_wav
from composerbench.client import ComposerBench
from composerbench.mcp.server import TOOLS, MCPServer
from composerbench.production import resample_audio

requires_ffmpeg = pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg optional')


def tone(rate=48000, seconds=2, frequency=440, level=0.15):
    signal = level * np.sin(2 * np.pi * frequency * np.arange(round(rate * seconds)) / rate)
    return np.column_stack([signal, signal])


def test_float_roundtrip_and_no_overwrite(tmp_path):
    original = tone()
    path = write_float_wav(tmp_path / 'tone.wav', original, 48000)
    actual, rate = read_wav(path)
    np.testing.assert_allclose(original, actual, atol=1e-8)
    assert rate == 48000
    with pytest.raises(FileExistsError):
        write_float_wav(path, original, rate)
    with pytest.raises(ValueError):
        write_float_wav(tmp_path / 'bad.wav', np.array([[np.nan]]), rate)


@pytest.mark.parametrize('rate', [44100, 48000, 96000])
def test_antiphase_bass_is_not_mistaken_for_missing_bass(rate):
    signal = tone(rate, frequency=70)
    signal[:, 1] *= -1
    measured = describe(signal, rate)
    assert measured['band_power_fraction']['sub_20_120'] > .99
    assert measured['stereo_correlation'] == pytest.approx(-1)
    assert measured['mono_loss_db'] < -60
    assert measured['low_band_mono_loss_db'] < -30
    assert {'mono_cancellation', 'low_end_cancellation'} <= {finding['kind'] for finding in measured['findings']}
    assert measured['rms_dbfs'] > -30


def test_diagnostic_gain_invariance_and_right_only_audio():
    original = tone()
    original[:, 0] = 0
    before, after = describe(original, 48000), describe(original * 2, 48000)
    assert before['stereo_correlation'] is None
    assert before['mono_loss_db'] == pytest.approx(-3.0103, abs=.001)
    assert before['band_power_fraction'] == pytest.approx(after['band_power_fraction'])
    assert after['rms_dbfs'] - before['rms_dbfs'] == pytest.approx(6.0206, abs=.001)


def test_timed_silence_findings_and_json():
    signal = np.concatenate([tone(seconds=1), np.zeros((48000, 2))])
    result = describe(signal, 48000, origin_seconds=12)
    silence = next(finding for finding in result['findings'] if finding['kind'] == 'quiet_window')
    assert silence['start_seconds'] == 13
    assert silence['end_seconds'] == 14
    json.dumps(result, allow_nan=False)


def test_invalid_excerpt_is_not_silently_truncated(tmp_path):
    path = write_float_wav(tmp_path / 'tone.wav', tone(), 48000)
    for start, duration in [(-1, 1), (1, 2), (3, None), (0, 0), (float('nan'), 1), (0, 181)]:
        with pytest.raises(ValueError):
            excerpt(path, start, duration)


def test_missing_meter_remains_explicit(tmp_path, monkeypatch):
    path = write_float_wav(tmp_path / 'tone.wav', tone(), 48000)
    monkeypatch.setattr(shutil, 'which', lambda _: None)
    result = inspect_audio(path)
    assert result['loudness']['status'] == 'unavailable'
    assert result['loudness']['integrated_lufs'] is None
    with pytest.raises(ValueError, match='FFmpeg'):
        compare_audio(path, path, tmp_path)


@requires_ffmpeg
def test_gain_only_candidate_cannot_win_by_being_louder(tmp_path):
    source = write_float_wav(tmp_path / 'before.wav', tone(seconds=4), 48000)
    candidate = write_float_wav(tmp_path / 'after.wav', tone(seconds=4) * 2, 48000)
    original = source.read_bytes()
    report = compare_audio(source, candidate, tmp_path)
    assert report['gain_only_change']
    assert report['quality_decision'] == 'not_automatically_judged'
    assert report['loudness_mismatch_lu'] <= .1
    assert report['verified_loudness'][0]['integrated_lufs'] == pytest.approx(-18, abs=.1)
    assert report['applied_gain_db'][1] - report['applied_gain_db'][0] == pytest.approx(-6.02, abs=.05)
    assert source.read_bytes() == original
    before, _rate = read_wav(report['files']['A'])
    after, _ = read_wav(report['files']['B'])
    measured_difference = 20 * np.log10(np.sqrt(np.mean(after**2)) / np.sqrt(np.mean(before**2)))
    assert abs(measured_difference) < .03
    assert all(Path(path).exists() for path in report['files'].values())
    json.dumps(report, allow_nan=False)


@requires_ffmpeg
def test_matched_pair_respects_true_peak_ceiling_without_limiting(tmp_path):
    signal = tone(seconds=4, level=.9)
    source = write_float_wav(tmp_path / 'before.wav', signal, 48000)
    report = compare_audio(source, source, tmp_path, ceiling_dbtp=-12)
    assert all(item['true_peak_dbtp'] <= -12 for item in report['verified_loudness'])
    matched, _ = read_wav(report['files']['A'])
    np.testing.assert_allclose(matched, signal * 10**(report['applied_gain_db'][0] / 20), atol=1e-7)


@requires_ffmpeg
def test_silence_is_not_a_valid_loudness_match(tmp_path):
    source = write_float_wav(tmp_path / 'silence.wav', np.zeros((48000, 2)), 48000)
    assert inspect_audio(source)['loudness']['integrated_lufs'] is None
    with pytest.raises(ValueError, match='non-silent'):
        compare_audio(source, source, tmp_path)


def test_mismatched_pairs_fail_before_export(tmp_path):
    source = write_float_wav(tmp_path / 'a.wav', tone(), 48000)
    other = write_float_wav(tmp_path / 'b.wav', tone(seconds=1), 48000)
    with pytest.raises(ValueError, match='equal sample rate'):
        compare_audio(source, other, tmp_path)


def test_slice_reverse_repeat_envelope_and_source_preservation(tmp_path):
    signal = np.linspace(-.2, .2, 8000)[:, None]
    source = write_float_wav(tmp_path / 'source.wav', signal, 8000)
    original = source.read_bytes()
    result = resample_audio(source, tmp_path, [{'start_seconds': 0, 'end_seconds': .5, 'reverse': True, 'repeat': 2}], fade_ms=0,
                            gain_points=[{'seconds': 0, 'db': -6}, {'seconds': 1, 'db': 0}])
    rendered, rate = read_wav(result['file'])
    expected = np.concatenate([signal[:4000][::-1]] * 2)
    expected *= 10**(np.linspace(-6, 0, 8000, endpoint=False)[:, None] / 20)
    np.testing.assert_allclose(rendered, expected, atol=1e-8)
    assert source.read_bytes() == original
    assert rate == 8000 and len(result['events']) == 2


def test_fades_remove_slice_boundary_discontinuities(tmp_path):
    source = write_float_wav(tmp_path / 'source.wav', np.full((48000, 2), .2), 48000)
    result = resample_audio(source, tmp_path, [{'start_seconds': .1, 'end_seconds': .2, 'repeat': 2}], fade_ms=5)
    rendered, _ = read_wav(result['file'])
    np.testing.assert_array_equal(rendered[[0, 4799, 4800, -1]], np.zeros((4, 2)))
    assert np.max(np.abs(np.diff(rendered[:, 0]))) < .001


@requires_ffmpeg
def test_octave_varispeed_halves_duration_and_doubles_frequency(tmp_path):
    source = write_float_wav(tmp_path / 'source.wav', tone(frequency=1000), 48000)
    result = resample_audio(source, tmp_path, [{'start_seconds': 0, 'end_seconds': 2, 'semitones': 12}])
    rendered, rate = read_wav(result['file'])
    assert len(rendered) / rate == pytest.approx(1, abs=.002)
    spectrum = np.abs(np.fft.rfft(rendered[:, 0]))
    frequency = np.fft.rfftfreq(len(rendered), 1 / rate)[spectrum.argmax()]
    assert frequency == pytest.approx(2000, abs=2)
    assert 'formants change together' in result['pitch_mode']


@pytest.mark.parametrize('slices,points', [([], None), ([{'start_seconds': 0, 'end_seconds': 3}], None),
    ([{'start_seconds': 0, 'end_seconds': 1}], [{'seconds': .5, 'db': 0}, {'seconds': .4, 'db': 0}]),
    ([{'start_seconds': 0, 'end_seconds': 1}], [{'seconds': 2, 'db': 0}]),
    ([{'start_seconds': 0, 'end_seconds': 1, 'gain_db': 12}], None)])
def test_invalid_recipes_do_not_modify_source_or_publish_outputs(tmp_path, slices, points):
    source = write_float_wav(tmp_path / 'source.wav', tone(level=.7), 48000)
    original = source.read_bytes()
    with pytest.raises(ValueError):
        resample_audio(source, tmp_path, slices, points)
    assert source.read_bytes() == original
    assert not list(tmp_path.glob('resample-*'))


def test_render_validation():
    for arguments in ({'start_beats': -1}, {'length_beats': 0}, {'bit_depth': 32}, {'start_beats': float('nan')}):
        with pytest.raises(ValueError):
            RenderRequest(Path('ignored.wav'), **arguments)


async def test_render_format_and_stem_fail_closed(tmp_path):
    client = ComposerBench.connect_fake(tmp_path)
    await client.create_track('lead')
    await client.create_clip('lead', 'notes', 0, 2)
    for depth in (16, 24):
        output = await client.render(RenderRequest(tmp_path / f'{depth}.wav', bit_depth=depth))
        with wave.open(str(output)) as audio:
            assert audio.getsampwidth() == depth // 8
    with pytest.raises(Exception, match='stems'):
        await client.render(RenderRequest(tmp_path / 'stem.wav', track_ids=['lead']))


@requires_ffmpeg
async def test_mcp_end_to_end_and_schemas(tmp_path):
    source = write_float_wav(tmp_path / 'source.wav', tone(), 48000)
    server = MCPServer(ComposerBench.connect_fake(tmp_path))
    for name, arguments in [
        ('music_audio_inspect', {'path': str(source)}),
        ('music_audio_compare', {'baseline': str(source), 'candidate': str(source)}),
        ('music_audio_resample', {'source': str(source), 'slices': [{'start_seconds': 0, 'end_seconds': 1, 'repeat': 2}]})]:
        result = await server.call_tool(name, arguments)
        assert not result.get('isError'), result
        assert isinstance(result['structuredContent'], dict)
    invalid = await server.call_tool('music_audio_resample', {'source': str(source), 'slices': [{'start_seconds': 0, 'end_seconds': 1, 'pitch_algorithm': 'invented'}]})
    assert invalid['isError']
    render = next(tool for tool in TOOLS if tool['name'] == 'music_render')
    assert render['inputSchema']['properties']['bit_depth']['default'] == 24


@requires_ffmpeg
async def test_rejected_revision_keeps_matched_audition_in_history(tmp_path):
    client = ComposerBench.connect_fake(tmp_path)
    await client.create_track('lead')
    await client.create_clip('lead', 'phrase', 0, 8)
    record = await client.revise('audition unchanged control', [],
                                 audition_start_seconds=0, audition_duration_seconds=2)
    assert record['decision'] == 'rejected'
    assert record['evaluation']['audition']['gain_only_change']
    assert Path(record['evaluation']['audition']['files']['A']).exists()
    assert client.revision_history()[-1]['evaluation']['audition']['status'] == 'ready_for_listening'
