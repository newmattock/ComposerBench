"""Per-section measurements of a mix and its stems: the numbers an agent uses
in place of listening ("is the drop louder and brighter than the verse?",
"does the bass vanish in the bridge?")."""
from __future__ import annotations

import math

import numpy as np
from scipy import signal

BANDS = {"sub": (25, 60), "low": (60, 250), "low_mid": (250, 1000), "mid": (1000, 4000),
         "high": (4000, 12000), "air": (12000, 20000)}


def _db(v: float) -> float | None:
    return round(10 * math.log10(v), 1) if v > 0 else None


def _psd(x: np.ndarray, sr: float):
    m = x.mean(axis=1) if x.ndim == 2 else x
    if len(m) < 2048:
        return None, None
    return signal.welch(m, sr, nperseg=4096, detrend=False)


def band_shares(x: np.ndarray, sr: float, psd=None) -> dict:
    """Share of spectral power per band, in dB relative to the total."""
    f, p = psd if psd is not None else _psd(x, sr)
    if f is None:
        return {}
    total = p[(f >= 20)].sum()
    if total <= 0:
        return {}
    return {name: _db(p[(f >= lo) & (f < min(hi, sr / 2))].sum() / total) for name, (lo, hi) in BANDS.items()}


def centroid_hz(x: np.ndarray, sr: float, psd=None) -> float | None:
    f, p = psd if psd is not None else _psd(x, sr)
    if f is None:
        return None
    s = p.sum()
    return round(float((f * p).sum() / s), 0) if s > 0 else None


def stereo_width(x: np.ndarray) -> float | None:
    if x.ndim != 2 or x.shape[1] < 2:
        return None
    mid = (x[:, 0] + x[:, 1]) / 2
    side = (x[:, 0] - x[:, 1]) / 2
    mr = math.sqrt(float(np.mean(mid ** 2)))
    return round(math.sqrt(float(np.mean(side ** 2))) / mr, 3) if mr > 0 else None


def segment_stats(x: np.ndarray, sr: float, xk: np.ndarray | None = None, full: bool = True) -> dict:
    """Stats of one segment; ``xk`` is the same segment already K-weighted."""
    from composerbench.mixing.loudness import integrated_lufs, integrated_lufs_k
    peak = float(np.abs(x).max(initial=0.0))
    rms = math.sqrt(float(np.mean(x ** 2))) if x.size else 0.0
    if len(x) < int(0.4 * sr):
        lufs = float("-inf")
    else:
        lufs = integrated_lufs_k(xk, sr) if xk is not None else integrated_lufs(x, sr)
    psd = _psd(x, sr)
    out = {"lufs": round(lufs, 1) if math.isfinite(lufs) else None,
           "peak_dbfs": round(20 * math.log10(peak), 1) if peak > 0 else None,
           "centroid_hz": centroid_hz(x, sr, psd)}
    if full:
        out.update(crest_db=round(20 * math.log10(peak / rms), 1) if rms > 0 and peak > 0 else None,
                   width=stereo_width(x), bands_db=band_shares(x, sr, psd))
    return out


def section_report(x: np.ndarray, sr: float, sections: list[dict], stems: dict | None = None) -> dict:
    """Measure each section ({"name", "start_s", "end_s"}) of the mix and optional stems.

    Returns {"mix": {section: stats}, "stems": {stem: {section: {lufs, peak, centroid}}},
    "contour": [(section, lufs)], "flags": [...]}.
    """
    from composerbench.mixing.loudness import k_weight
    out = {"mix": {}, "stems": {}, "contour": [], "flags": []}
    xk = k_weight(x, sr)
    for s in sections:
        a, b = int(s["start_s"] * sr), int(s["end_s"] * sr)
        seg = x[a:b]
        if len(seg) < int(0.4 * sr):
            continue
        st = segment_stats(seg, sr, xk[a:b])
        out["mix"][s["name"]] = st
        out["contour"].append((s["name"], st["lufs"]))
    for name, y in (stems or {}).items():
        yk = k_weight(y, sr)
        row = {}
        for s in sections:
            a, b = int(s["start_s"] * sr), int(s["end_s"] * sr)
            seg = y[a:b]
            if len(seg) < int(0.4 * sr):
                continue
            row[s["name"]] = segment_stats(seg, sr, yk[a:b], full=False)
        out["stems"][name] = row
    lufs = [v for _, v in out["contour"] if v is not None]
    if lufs and max(lufs) - min(lufs) < 1.0 and len(lufs) > 2:
        out["flags"].append("every section is within 1 LU: the arrangement has no loudness contour")
    for name, st in out["mix"].items():
        if st["peak_dbfs"] is not None and st["peak_dbfs"] > -0.1:
            out["flags"].append(f"{name}: peaks at {st['peak_dbfs']} dBFS")
    return out
