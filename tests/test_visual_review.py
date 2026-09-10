import json

import numpy as np
import pytest

from composerbench.analysis.visual import overlap_evidence, spectral_map, stereo_bands, time_envelope, visual_review
from composerbench.analysis.wav import write_float_wav


def tone(frequency=100, rate=8000, seconds=1):
    samples = .25 * np.sin(2 * np.pi * frequency * np.arange(round(rate * seconds)) / rate)
    return samples[:, None]


def test_psd_preserves_energy_and_antiphase():
    samples = tone()
    frequencies, times, power = spectral_map(samples, 8000, 2048)
    assert frequencies[np.argmax(power.mean(axis=1))] == pytest.approx(100, abs=4)
    assert power.sum(axis=0).mean() * (frequencies[1] - frequencies[0]) == pytest.approx(.25**2 / 2, rel=.01)
    stereo = np.concatenate([samples, -samples], axis=1)
    np.testing.assert_allclose(spectral_map(stereo, 8000, 2048)[2], power)
    band = stereo_bands(stereo, 8000)[0]
    assert band['correlation'] == pytest.approx(-1)
    assert band['mono_loss_db'] < -100


def test_overlap_rejects_silence_and_distinct_tones():
    frequencies, times, first = spectral_map(tone(), 8000, 2048)
    second = spectral_map(tone(1800), 8000, 2048)[2]
    assert overlap_evidence(first, first, frequencies, times, 30)['mean_shared_power_ratio'] == pytest.approx(1)
    assert overlap_evidence(first, second, frequencies, times, 30)['mean_shared_power_ratio'] < .01
    assert overlap_evidence(first, np.zeros_like(first), frequencies, times, 30)['regions'] == []


def test_peak_envelope_retains_single_sample_transient():
    samples = np.zeros((8000, 2))
    samples[4123] = 1.2
    envelope = time_envelope(samples, 8000, columns=20)
    assert max(envelope['peak_dbfs']) == pytest.approx(20 * np.log10(1.2))
    assert max(envelope['rms_dbfs']) < -20


def test_report_is_local_finite_and_escaped(tmp_path):
    pytest.importorskip('matplotlib')
    source = write_float_wav(tmp_path / 'silence.wav', np.zeros((8000, 2)), 8000)
    report = visual_review(source, tmp_path, duration_seconds=1, stems={'<script>': str(source)})
    assert report['status'] == 'technical_evidence_only'
    assert report['interactions'] == []
    from pathlib import Path
    content = Path(report['files']['html']).read_text()
    assert '&lt;script&gt;' in content and '<script>' not in content
    assert 'https://' not in content
    assert json.loads(Path(report['files']['json']).read_text())['mix_loudness']['integrated_lufs'] is None
    assert all(Path(path).stat().st_size > 1000 for path in report['files']['images'])


def test_mismatched_stem_rate_and_bad_input_fail(tmp_path):
    pytest.importorskip('matplotlib')
    first = write_float_wav(tmp_path / 'a.wav', tone(), 8000)
    second = write_float_wav(tmp_path / 'b.wav', tone(rate=16000), 16000)
    with pytest.raises(ValueError, match='equal sample rate'):
        visual_review(first, tmp_path, duration_seconds=1, stems={'bass': str(second)})
    with pytest.raises(ValueError):
        visual_review(first, tmp_path, start_seconds=-1)
    with pytest.raises(ValueError, match='eight'):
        visual_review(first, tmp_path, stems={str(index): str(first) for index in range(9)})


@pytest.mark.asyncio
async def test_facade(tmp_path):
    pytest.importorskip('matplotlib')
    from composerbench.client import ComposerBench
    source = write_float_wav(tmp_path / 'tone.wav', tone(), 8000)
    client = ComposerBench.connect_fake(tmp_path / 'session')
    report = await client.audio_visual_review(path=str(source), duration_seconds=1)
    assert report['duration_seconds'] == 1


@pytest.mark.asyncio
async def test_mcp_visual_tool(tmp_path):
    pytest.importorskip('matplotlib')
    from composerbench.mcp.server import MCPServer, ServerConfig, build_client
    source = write_float_wav(tmp_path / 'tone.wav', tone(), 8000)
    server = MCPServer(build_client(ServerConfig(project_path=str(tmp_path / 'session'))))
    result = await server.call_tool('music_audio_visual_review', {'path': str(source), 'duration_seconds': 1})
    assert not result.get('isError')
    assert result['structuredContent']['status'] == 'technical_evidence_only'
