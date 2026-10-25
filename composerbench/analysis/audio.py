"""Deterministic audio measurements over rendered files (spec §30, §51).

Reads PCM/float WAV, reports sample peak/RMS/crest/DC. LUFS/spectral
metrics arrive later behind an optional dependency boundary.
"""

from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioStats:
    duration_seconds: float
    sample_rate: int
    channels: int
    peak_dbfs: float
    rms_dbfs: float
    crest_factor_db: float
    dc_offset: float

    def to_dict(self) -> dict:
        return {
            "duration_seconds": round(self.duration_seconds, 3),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "peak_dbfs": round(self.peak_dbfs, 2),
            "rms_dbfs": round(self.rms_dbfs, 2),
            "crest_factor_db": round(self.crest_factor_db, 2),
            "dc_offset": round(self.dc_offset, 6),
        }


def _dbfs(x: float) -> float:
    return -120.0 if x <= 0.0 else max(-120.0, 20.0 * math.log10(x))


def analyze_wav(path: Path | str) -> AudioStats:
    import numpy as np

    from composerbench.analysis.wav import read_wav
    samples, sample_rate = read_wav(path)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
    peak_db, rms_db = _dbfs(peak), _dbfs(rms)
    return AudioStats(
        duration_seconds=len(samples) / sample_rate,
        sample_rate=sample_rate, channels=samples.shape[1],
        peak_dbfs=peak_db, rms_dbfs=rms_db,
        crest_factor_db=max(0.0, peak_db - rms_db),
        dc_offset=float(samples.mean()) if samples.size else 0.0,
    )


def delta_stats(before: AudioStats, after: AudioStats) -> dict:
    """Causal before/after summary for the revision loop (spec §26, §54)."""
    return {
        "loudness_delta_lu_approx": round(after.rms_dbfs - before.rms_dbfs, 2),
        "peak_delta_db": round(after.peak_dbfs - before.peak_dbfs, 2),
        "duration_delta_s": round(after.duration_seconds - before.duration_seconds, 3),
    }


def normalize_wav(path: Path | str, target_dbfs: float = -1.0) -> AudioStats:
    """Scale a 16-bit PCM WAV so its peak hits target_dbfs (in place).

    Mastering-grade headroom for agent-produced renders: full-scale writes
    clip int16 and destroy transients.
    """
    path = Path(path)
    with wave.open(str(path), "rb") as wf:
        params = wf.getparams()
        if wf.getsampwidth() != 2:
            raise ValueError("normalize_wav requires 16-bit PCM")
        raw = wf.readframes(wf.getnframes())
    count = len(raw) // 2
    samples = list(struct.unpack(f"<{count}h", raw))
    peak = max((abs(s) for s in samples), default=0)
    if peak == 0:
        return analyze_wav(path)
    target_peak = int(32767 * (10 ** (target_dbfs / 20)))
    gain = target_peak / peak
    scaled = [max(-32768, min(32767, round(s * gain))) for s in samples]
    with wave.open(str(path), "wb") as wf:
        wf.setparams(params)
        wf.writeframes(struct.pack(f"<{count}h", *scaled))
    return analyze_wav(path)
