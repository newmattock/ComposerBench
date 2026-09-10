"""Critic + features tests (Sprint B1/B2)."""
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from composerbench.analysis.features import section_features
from composerbench.critique.critic import criticize


def _write_tone(path: Path, seconds: float, freq: float, amp: float = 0.5,
                sr: int = 44100):
    t = np.arange(int(seconds * sr)) / sr
    data = (amp * np.sin(2 * math.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def test_features_detect_brightness_difference(tmp_path):
    dark = tmp_path / "dark.wav"
    bright = tmp_path / "bright.wav"
    _write_tone(dark, 2.0, 220.0)     # A3
    _write_tone(bright, 2.0, 3520.0)  # A7 — 3 octaves up
    bounds = [(0.0, 4.0)]
    f_dark = section_features(dark, bounds)[0]
    f_bright = section_features(bright, bounds)[0]
    assert f_bright["centroid_hz"] > f_dark["centroid_hz"] * 5
    assert f_bright["high_energy"] > f_dark["high_energy"]


def test_features_rms_levels(tmp_path):
    quiet = tmp_path / "q.wav"
    loud = tmp_path / "l.wav"
    _write_tone(quiet, 1.5, 440.0, amp=0.05)
    _write_tone(loud, 1.5, 440.0, amp=0.9)
    q = section_features(quiet, [(0, 4)])[0]["rms_dbfs"]
    l = section_features(loud, [(0, 4)])[0]["rms_dbfs"]
    assert l - q > 20   # ~25 dB expected


def test_critic_flags_too_dark():
    feats = [{"rms_dbfs": -14.0, "centroid_hz": 120.0, "low_energy": 0.2,
              "mid_energy": 0.6, "high_energy": 0.2, "stereo_width": 0.1,
              "transient_density": 2.0}]
    findings = criticize(feats, ["glow"], {"overall": ["bright"]})
    dims = [f["dimension"] for f in findings]
    assert "brightness" in dims
    hit = next(f for f in findings if f["dimension"] == "brightness")
    assert hit["section"] == "glow"
    assert 0 < hit["severity"] <= 1


def test_critic_flags_arc_failure():
    # targets rise dawn->glow but rendered levels fall
    feats = [
        {"rms_dbfs": -12.0, "centroid_hz": 900, "low_energy": 0.2,
         "transient_density": 2},
        {"rms_dbfs": -18.0, "centroid_hz": 900, "low_energy": 0.2,
         "transient_density": 2},
    ]
    findings = criticize(feats, ["dawn", "glow"],
                         energy_targets={"dawn": 0.42, "glow": 0.80})
    arcs = [f for f in findings if f["dimension"] == "arc"]
    assert arcs and arcs[0]["section"] == "glow"


def test_critic_clean_when_matching():
    feats = [{"rms_dbfs": -13.0, "centroid_hz": 900, "low_energy": 0.2,
              "transient_density": 2}]
    findings = criticize(feats, ["bloom"], {"overall": ["warm"]})
    serious = [f for f in findings if f["severity"] > 0.5]
    assert not serious
