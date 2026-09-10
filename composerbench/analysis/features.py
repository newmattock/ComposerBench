"""Section-aware audio features (Sprint B1).

Pure-numpy extraction over the harness's existing wave reading: per-section
RMS, spectral centroid, 3-band energy split, stereo width, transient density.
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np


def section_features(path: Path, boundaries_beats: list[tuple[float, float]],
                     bpm: float = 120.0) -> list[dict]:
    """features per section [(start_beat, end_beat), ...] in beats."""
    from composerbench.analysis.wav import read_wav
    samples, sr = read_wav(path)
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError("bpm must be finite and positive")
    feats = []
    for (b0, b1) in boundaries_beats:
        if not all(math.isfinite(b) for b in (b0, b1)) or b0 < 0 or b1 <= b0:
            raise ValueError("section bounds require 0 <= start < end")
        i0 = int(b0 * 60.0 / bpm * sr)
        i1 = min(int(b1 * 60.0 / bpm * sr), len(samples))
        seg = samples[i0:i1]
        if len(seg) == 0:
            feats.append({"rms_dbfs": -120.0, "centroid_hz": 0.0,
                          "low_energy": 0.0, "mid_energy": 0.0,
                          "high_energy": 0.0, "stereo_width": 0.0,
                          "transient_density": 0.0})
            continue
        rms = float(np.sqrt(np.mean(seg ** 2)))
        rms_dbfs = 20 * math.log10(max(rms, 1e-9))

        # spectral centroid via rfft of the whole section (good enough for
        # section-level brightness comparisons)
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))[:, None], axis=0)).mean(axis=1)
        freqs = np.fft.rfftfreq(len(seg), 1.0 / sr)
        total = spec.sum() or 1.0
        centroid = float((freqs * spec).sum() / total)

        low = float(spec[freqs < 250].sum() / total)
        mid = float(spec[(freqs >= 250) & (freqs < 2000)].sum() / total)
        high = float(spec[freqs >= 2000].sum() / total)

        width = 0.0
        if samples.shape[1] == 2:
            sseg = seg[:, 1]
            left = seg[:, 0]
            m_rms = float(np.sqrt(np.mean(((left + sseg) / 2) ** 2))) or 1e-9
            s_rms = float(np.sqrt(np.mean(((left - sseg) / 2) ** 2)))
            width = s_rms / m_rms

        # transient density: frames whose energy jumps >3x the local median
        hop = 1024
        n_frames = len(seg) // hop
        energies = np.array([
            np.sqrt(np.mean(seg[k * hop:(k + 1) * hop] ** 2))
            for k in range(n_frames)]) if n_frames else np.zeros(1)
        med = np.median(energies) or 1e-9
        onsets = int(np.sum((energies[1:] > 3 * energies[:-1])
                            & (energies[1:] > med)))
        density = onsets / max(i1 - i0, 1) * sr   # onsets per second

        feats.append({"rms_dbfs": rms_dbfs, "centroid_hz": centroid,
                      "low_energy": low, "mid_energy": mid,
                      "high_energy": high, "stereo_width": width,
                      "transient_density": density})
    return feats
