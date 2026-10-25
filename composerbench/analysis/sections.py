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
    out["flags"].extend(balance_flags(out, sections))
    return out


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def balance_flags(report: dict, sections: list[dict], tilt_db: float = 10.0, mono_width: float = 0.06,
                  buried_db: float = 15.0) -> list[str]:
    """Problems a listener would hear at once, from a section report.

    - a section louder than a section it should build to (declared ``energy``)
    - a bass-heavy tilt (mids far under the low end) or no top end
    - a nearly mono mix
    - a stem buried far under the loudest stem wherever it plays (fx stems excepted)
    """
    flags = []
    mix = report.get("mix", {})
    lufs = {n: st["lufs"] for n, st in mix.items() if st.get("lufs") is not None}
    energy = {s["name"]: s["energy"] for s in sections if s.get("energy") is not None and s["name"] in lufs}
    for name, e in energy.items():
        higher = [m for m, f in energy.items() if f >= e + 0.3]
        if higher:
            ref = min(higher, key=lambda m: lufs[m])
            if lufs[name] > lufs[ref] + 0.5:
                flags.append(f"{name} (energy {e}) is louder than {ref} (energy {energy[ref]}): "
                             f"{lufs[name]} vs {lufs[ref]} LUFS - the arrangement does not build")
    bands = [st.get("bands_db") or {} for st in mix.values()]
    low = _median(max((b.get("sub") or -99), (b.get("low") or -99)) for b in bands if b)
    mid = _median(b.get("mid") for b in bands if b)
    high = _median(b.get("high") for b in bands if b)
    if low is not None and mid is not None and low - mid > tilt_db:
        flags.append(f"bass-heavy: the 1-4 kHz band sits {low - mid:.1f} dB under the low end "
                     f"(typical pop/EDM: 3-8 dB); raise the melodic parts or cut sub/808")
    if high is not None and high < -25:
        flags.append(f"dull: only {high} dB of the energy is in 4-12 kHz; the mix has no top end")
    width = _median(st.get("width") for st in mix.values())
    if width is not None and width < mono_width:
        flags.append(f"nearly mono: median side/mid ratio {width:.3f}; widen pads/synths or pan parts")
    stems = report.get("stems", {})
    for stem, row in stems.items():
        if "fx" in stem.lower():
            continue
        gaps = []
        for sec, st in row.items():
            if st.get("lufs") is None or st["lufs"] < -60:
                continue
            loudest = max((r[sec]["lufs"] for r in stems.values() if sec in r and r[sec].get("lufs") is not None),
                          default=None)
            if loudest is not None:
                gaps.append(loudest - st["lufs"])
        gap = _median(gaps)
        if gap is not None and gap > buried_db:
            flags.append(f"{stem} is buried: {gap:.1f} dB under the loudest stem where it plays")
    return flags
