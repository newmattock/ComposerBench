import math
import struct
import wave
from pathlib import Path

import pytest

from composerbench.analysis.audio import analyze_wav, delta_stats
from composerbench.errors import EntityNotFound


def write_tone(path: Path, freq: float = 440.0, seconds: float = 0.5, amp: float = 0.5):
    sr = 44100
    n = int(seconds * sr)
    frames = b"".join(
        struct.pack("<h", int(amp * 32767.0 * math.sin(2 * math.pi * freq * i / sr)))
        for i in range(n)
    )
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(frames)


def test_missing_file_is_structured_error(tmp_path):
    with pytest.raises(EntityNotFound):
        analyze_wav(tmp_path / "nope.wav")


def test_tone_measurements_are_physical(tmp_path):
    p = tmp_path / "tone.wav"
    write_tone(p, amp=0.5)
    stats = analyze_wav(p).to_dict()
    assert stats["duration_seconds"] == pytest.approx(0.5, abs=0.001)
    assert stats["peak_dbfs"] == pytest.approx(-6.02, abs=0.1)   # 0.5 full scale
    assert stats["rms_dbfs"] == pytest.approx(-9.03, abs=0.15)   # sine: rms = amp/sqrt(2)
    assert stats["crest_factor_db"] == pytest.approx(3.01, abs=0.1)
    assert abs(stats["dc_offset"]) < 0.001


def test_quiet_file_has_floor_not_negative_infinity(tmp_path):
    p = tmp_path / "quiet.wav"
    write_tone(p, amp=0.001)
    stats = analyze_wav(p)
    assert -80 < stats.rms_dbfs < 0          # finite measurement floor
    assert stats.crest_factor_db >= 0


def test_delta_stats_direction():
    analyze_wav.__wrapped__ if hasattr(analyze_wav, "__wrapped__") else None
    from composerbench.analysis.audio import AudioStats

    a = AudioStats(1.0, 44100, 2, -12.0, -18.0, 6.0, 0.0)
    b = AudioStats(1.0, 44100, 2, -10.0, -14.0, 4.0, 0.0)
    d = delta_stats(a, b)
    assert d["loudness_delta_lu_approx"] == 4.0
    assert d["peak_delta_db"] == 2.0
