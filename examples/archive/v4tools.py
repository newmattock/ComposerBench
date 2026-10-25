"""Dusty-v4 analysis toolkit: my substitute for ears.
Numeric band energies per section + loudness shape + A/B diff tables."""
import contextlib
import json
import wave
from pathlib import Path

import numpy as np

SECTION_BOUNDS = {"intro": (0, 16), "A": (16, 48), "B": (48, 80),
                  "A2": (80, 112), "outro": (112, 128)}
BPM = 72.0

BANDS = [("sub", 20, 60), ("bass", 60, 150), ("lowmid", 150, 400),
         ("mid", 400, 1000), ("upmid", 1000, 2500),
         ("pres", 2500, 6000), ("air", 6000, 14000)]


def load(path):
    with contextlib.closing(wave.open(str(path))) as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 2:
        x = x.reshape(-1, 2).mean(axis=1)
    return x, sr


def spectrum_db(x, sr):
    """Windowed RMS spectrum (Hann), returned as (freqs, db)."""
    n = 8192
    hop = n // 2
    win = np.hanning(n)
    frames = max(1, (len(x) - n) // hop)
    acc = None
    for i in range(frames):
        seg = x[i * hop:i * hop + n] * win
        mag = np.abs(np.fft.rfft(seg)) / n
        acc = mag if acc is None else acc + mag
    spec = acc / max(1, frames)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    db = 20 * np.log10(spec + 1e-10)
    return freqs, db


def band_db(freqs, db, lo, hi):
    m = (freqs >= lo) & (freqs < hi)
    return float(db[m].mean()) if m.any() else -120.0


def centroid_of(freqs, db):
    lin = 10 ** (db / 20.0)
    if lin.sum() <= 0:
        return 0.0
    return float((freqs * lin).sum() / lin.sum())


def report(path):
    x, sr = load(path)
    dur = len(x) / sr
    spf = dur / 128.0  # seconds per beat (128-beat form)
    freqs, db_full = spectrum_db(x, sr)
    out = {"file": Path(path).name, "dur": round(dur, 2),
           "peak": round(20 * np.log10(abs(x).max() + 1e-9), 2),
           "rms": round(20 * np.log10(np.sqrt((x ** 2).mean())), 2)}
    print(f"\n== {out['file']}  {dur:.2f}s  peak {out['peak']} dBFS  "
          f"rms {out['rms']} dBFS ==")
    hdr = "section   " + "".join(f"{n:>8}" for n, _, _ in BANDS) \
        + "   cent_Hz"
    print(hdr)
    rows = {}
    for name, (b0, b1) in SECTION_BOUNDS.items():
        s0 = int(b0 * spf * sr)
        s1 = min(len(x), int(b1 * spf * sr))
        seg = x[s0:s1]
        f, d = spectrum_db(seg, sr)
        vals = [band_db(f, d, lo, hi) for _, lo, hi in BANDS]
        rows[name] = {"bands": dict(zip([n for n, _, _ in BANDS],
                                        [round(v, 1) for v in vals])),
                      "centroid": round(centroid_of(f, d))}
        print(f"{name:9s} " + "".join(f"{v:8.1f}" for v in vals)
              + f"   {rows[name]['centroid']:>7}")
    # loudness per 8 bars (phrase shape)
    phrase_rms = []
    for k in range(16):
        s0 = int(k * 8 * spf * sr)
        s1 = min(len(x), int((k + 1) * 8 * spf * sr))
        seg = x[s0:s1]
        phrase_rms.append(round(
            20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9), 1))
    print("phrase RMS:", phrase_rms)
    return out, rows


def diff(ra, rb):
    """rb relative to ra (post-fix vs previous take)."""
    print(f"\n== DELTA {rb['file']} vs {ra['file']} ==")
    print(f"dur {ra['dur']} -> {rb['dur']}   rms {ra['rms']} -> {rb['rms']}"
          f"   peak {ra['peak']} -> {rb['peak']}")
    for sec in SECTION_BOUNDS:
        ca, cb = ra[sec]["centroid"], rb[sec]["centroid"]
        moves = []
        for b in ra[sec]["bands"]:
            d = rb[sec]["bands"][b] - ra[sec]["bands"][b]
            if abs(d) >= 1.0:
                moves.append(f"{b}{'+' if d > 0 else ''}{d:.1f}")
        print(f"{sec:9s} cent {ca:>5} -> {cb:>5}   {' '.join(moves)}")


if __name__ == "__main__":
    import sys
    paths = sys.argv[1:]
    reps = []
    for p in paths:
        r = report(p)
        reps.append(r)
    for i in range(1, len(reps)):
        diff(reps[i - 1][1], reps[i][1])
