import struct

import numpy as np
import pytest

from composerbench.analysis.audio import analyze_wav
from composerbench.analysis.features import section_features
from composerbench.composition.state import CompositionPlan, Section


def wav(path, data, sr=48000, bits=16, floating=False):
    data = np.asarray(data)
    if data.ndim == 1: data = data[:, None]
    channels = data.shape[1]
    if floating:
        raw = data.astype('<f4').tobytes(); bits = 32
    elif bits == 24:
        n = (data.ravel()*8388607).astype(np.int32)
        raw = np.stack([n & 255, (n >> 8) & 255, (n >> 16) & 255], axis=1).astype('u1').tobytes()
    else: raw = (data*32767).astype('<i2').tobytes()
    fmt = struct.pack('<HHIIHH', 3 if floating else 1, channels, sr,
                      sr*channels*(bits//8), channels*(bits//8), bits)
    body = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(raw)) + raw
    path.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)
    return path


@pytest.mark.parametrize('sr', [44100, 48000, 96000])
@pytest.mark.parametrize('bits,floating', [(16,False), (24,False), (32,True)])
def test_sample_rate_formats_and_stereo(tmp_path, sr, bits, floating):
    tone = .5*np.sin(2*np.pi*1000*np.arange(sr)/sr)
    p = wav(tmp_path/'tone.wav', np.column_stack([np.zeros(sr),tone]), sr, bits, floating)
    stats = analyze_wav(p)
    f = section_features(p, [(0,2)], 120)[0]
    assert stats.duration_seconds == 1
    assert stats.sample_rate == sr
    assert f['centroid_hz'] == pytest.approx(1000, abs=10)
    assert f['rms_dbfs'] == pytest.approx(-12.04, abs=.02)
    assert f['stereo_width'] == pytest.approx(1)


def test_phase_inversion_does_not_erase_spectral_content(tmp_path):
    tone = .5*np.sin(2*np.pi*1000*np.arange(48000)/48000)
    p = wav(tmp_path/'phase.wav', np.column_stack([tone,-tone]))
    assert section_features(p, [(0,2)])[0]['centroid_hz'] == pytest.approx(1000, abs=10)


def test_nonfinite_audio_is_rejected(tmp_path):
    p = wav(tmp_path/'bad.wav', [0,float('nan')], floating=True)
    with pytest.raises(ValueError, match='NaN'): analyze_wav(p)


def test_musical_boundaries():
    for meter in (3,4):
        plan = CompositionPlan(beats_per_bar=meter, sections=[Section('intro',2,1),Section('verse',2,3)])
        assert plan.section_bounds() == [(0,2*meter), (2*meter,4*meter)]
