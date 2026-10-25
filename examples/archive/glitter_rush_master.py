"""GLITTER RUSH — stem mixdown + master (post-ComposerBench, numpy/scipy only).

ComposerBench/MAGDA have no master bus, sends, sidechain or automation yet, so
this step does the parts of production that need them, using the *score*
(score.json) as ground truth instead of guessing from audio:

  1. MIDI-keyed sidechain: every kick in the score ducks synths/lead/bass
     (depth depends on section: deep in drops, light in verses).
  2. Glitch edits on the timeline: reversed swell in the silent beat before
     each drop (built with composerbench.production.resample_audio when the
     package is importable), a tape-stop at the end of drop 1, and a
     band-limited "phone" intro that opens up by bar 4.
  3. Stem balance by K-weighted loudness measured over drop 1.
  4. Mid/side low-end mono, tone EQ, glue compression, oversampled soft clip
     and look-ahead limiting to a loudness target with a true-peak ceiling.

usage: python glitter_rush_master.py --src ~/Music/ComposerBench/glitter_rush \
                                     --out ~/Music/ComposerBench/glitter_rush
needs numpy + scipy (+ ffmpeg on PATH for the MP3); composerbench is used for the
reverse-swell slices when importable, with a numpy fallback.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import struct
import subprocess
import sys

import numpy as np
from scipy import signal
from scipy.ndimage import minimum_filter1d, uniform_filter1d

STEMS = ["drums", "bass", "synths", "lead", "fx"]


# ------------------------------------------------------------------ io ----
def read_wav(path):
    raw = pathlib.Path(path).read_bytes()
    pos, fmt, data = 12, None, b""
    while pos + 8 <= len(raw):
        kind, size = struct.unpack_from("<4sI", raw, pos)
        pos += 8
        if kind == b"fmt ":
            fmt = raw[pos:pos + size]
        elif kind == b"data":
            data += raw[pos:pos + size]
        pos += size + (size & 1)
    tag, ch, sr, _, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if tag == 0xFFFE:
        tag = struct.unpack_from("<H", fmt, 24)[0]
    if tag == 1 and bits == 24:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        x = ((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) ^ 0x800000) - 0x800000
        x = x.astype(np.float64) / 8388608.0
    elif tag == 1 and bits == 16:
        x = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    elif tag == 3 and bits == 32:
        x = np.frombuffer(data, dtype="<f4").astype(np.float64)
    else:
        raise ValueError(f"unsupported wav {tag}/{bits}")
    x = x.reshape(-1, ch)
    if ch == 1:
        x = np.repeat(x, 2, axis=1)
    return x, sr


def write_wav24(path, x, sr):
    x = np.clip(x, -1.0, 1.0 - 2 ** -23)
    q = np.round(x * 8388608.0).astype(np.int32).reshape(-1)
    b = np.empty((q.size, 3), dtype=np.uint8)
    b[:, 0] = q & 0xFF
    b[:, 1] = (q >> 8) & 0xFF
    b[:, 2] = (q >> 16) & 0xFF
    data = b.tobytes()
    fmt = struct.pack("<HHIIHH", 1, 2, sr, sr * 6, 6, 24)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data
    pathlib.Path(path).write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


# ------------------------------------------------------------ metering ----
def _k_sos(sr):
    G, fc, Q = 4.0, 1681.974450955533, 0.7071752369554196
    A, w0 = 10 ** (G / 40), 2 * math.pi * fc / sr
    al, c = math.sin(w0) / (2 * Q), math.cos(w0)
    shelf = [A * ((A + 1) + (A - 1) * c + 2 * math.sqrt(A) * al), -2 * A * ((A - 1) + (A + 1) * c),
             A * ((A + 1) + (A - 1) * c - 2 * math.sqrt(A) * al),
             (A + 1) - (A - 1) * c + 2 * math.sqrt(A) * al, 2 * ((A - 1) - (A + 1) * c),
             (A + 1) - (A - 1) * c - 2 * math.sqrt(A) * al]
    fc, Q = 38.13547087602444, 0.5003270373238773
    w0 = 2 * math.pi * fc / sr
    al, c = math.sin(w0) / (2 * Q), math.cos(w0)
    hp = [(1 + c) / 2, -(1 + c), (1 + c) / 2, 1 + al, -2 * c, 1 - al]
    sos = []
    for b0, b1, b2, a0, a1, a2 in (shelf, hp):
        sos.append([b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0])
    return np.array(sos)


def lufs(x, sr):
    """ITU-R BS.1770-4 integrated loudness (gated)."""
    if len(x) < int(0.4 * sr):
        return -70.0
    y = signal.sosfilt(_k_sos(sr), x, axis=0)
    blk, hop = int(0.4 * sr), int(0.1 * sr)
    p = np.cumsum(np.concatenate([np.zeros((1, 2)), y ** 2]), axis=0)
    starts = np.arange(0, len(y) - blk + 1, hop)
    ms = (p[starts + blk] - p[starts]) / blk
    z = ms.sum(axis=1)
    lk = -0.691 + 10 * np.log10(np.maximum(z, 1e-12))
    z = z[lk > -70]
    if not len(z):
        return -70.0
    rel = -0.691 + 10 * np.log10(z.mean()) - 10
    z2 = z[(-0.691 + 10 * np.log10(z)) > rel]
    return float(-0.691 + 10 * np.log10(z2.mean())) if len(z2) else -70.0


def short_term(x, sr, win=3.0, hop=1.0):
    y = signal.sosfilt(_k_sos(sr), x, axis=0)
    blk, h = int(win * sr), int(hop * sr)
    p = np.cumsum(np.concatenate([np.zeros((1, 2)), y ** 2]), axis=0)
    out = []
    for s in range(0, max(1, len(y) - blk + 1), h):
        z = ((p[s + blk] - p[s]) / blk).sum()
        out.append(-0.691 + 10 * math.log10(max(z, 1e-12)))
    return out


def true_peak_db(x):
    up = signal.resample_poly(x, 4, 1, axis=0)
    return 20 * math.log10(max(np.abs(up).max(), 1e-12))


# ------------------------------------------------------------- filters ----
def biquad(kind, f, sr, q=0.707, gain_db=0.0):
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f / sr
    al, c = math.sin(w0) / (2 * q), math.cos(w0)
    if kind == "peak":
        b, a = [1 + al * A, -2 * c, 1 - al * A], [1 + al / A, -2 * c, 1 - al / A]
    elif kind == "lowshelf":
        sq = 2 * math.sqrt(A) * al
        b = [A * ((A + 1) - (A - 1) * c + sq), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - sq)]
        a = [(A + 1) + (A - 1) * c + sq, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - sq]
    elif kind == "highshelf":
        sq = 2 * math.sqrt(A) * al
        b = [A * ((A + 1) + (A - 1) * c + sq), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sq)]
        a = [(A + 1) - (A - 1) * c + sq, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sq]
    else:
        raise ValueError(kind)
    return np.array([[b[0] / a[0], b[1] / a[0], b[2] / a[0], 1.0, a[1] / a[0], a[2] / a[0]]])


def eq(x, sr, bands):
    for kind, f, q, g in bands:
        x = signal.sosfilt(biquad(kind, f, sr, q, g), x, axis=0)
    return x


# ------------------------------------------------------------ dynamics ----
def duck_curve(n, sr, times_s, depth_db_at, attack=0.004, release=0.13):
    """Gain curve (linear) that dips after every trigger time."""
    L = int((attack + 6 * release) * sr)
    t = np.arange(L) / sr
    kern = np.where(t < attack, t / attack, np.exp(-(t - attack) / release))
    idx = (np.asarray(times_s) * sr).astype(int)
    idx = idx[(idx >= 0) & (idx < n)]
    depth = np.array([depth_db_at(i / sr) for i in idx])
    # max-combine overlapping dips (a sum would over-duck fast kick runs)
    env = np.zeros(n + L)
    for i, d in zip(idx, depth):
        seg = env[i:i + L]
        np.maximum(seg, d * kern, out=seg)
    return 10 ** (-env[:n] / 20)


def glue(x, sr, thresh_db=-16.0, ratio=2.0, win=0.03, smooth=0.06):
    lvl = uniform_filter1d((x ** 2).mean(axis=1), int(win * sr))
    db = 10 * np.log10(np.maximum(lvl, 1e-12))
    gr = np.minimum(0.0, (thresh_db - db) * (1 - 1 / ratio))
    gr = uniform_filter1d(gr, int(smooth * sr))
    return x * (10 ** (gr / 20))[:, None], float(gr.min())


def soft_clip(x, drive_db=2.0):
    k = 10 ** (drive_db / 20)
    return np.tanh(x * k) / math.tanh(k) if drive_db > 0 else x


def window_min(v, back, ahead):
    """out[n] = min(v[n-back : n+ahead+1]) (edges padded with 1.0)."""
    size = back + ahead + 1
    pad = np.concatenate([np.ones(back), v, np.ones(ahead)])
    return minimum_filter1d(pad, size=size, origin=-(size // 2))[:len(v)]


def limit(x, sr, ceiling_db=-1.2, lookahead=0.0015, hold=0.012, os=4):
    """Look-ahead brickwall on a 4x-oversampled signal (vectorised).

    gain[n] = boxcar_{2la+1}( min(need[n-hold .. n+la]) ): the average at a peak
    only spans samples whose window contains that peak, so gain <= need there.
    """
    up = signal.resample_poly(x, os, 1, axis=0)
    c = 10 ** (ceiling_db / 20)
    need = np.minimum(1.0, c / np.maximum(np.abs(up).max(axis=1), 1e-12))
    la, hd = int(lookahead * sr * os), max(int(hold * sr * os), int(lookahead * sr * os))
    g = window_min(need, hd, la)
    g = uniform_filter1d(g, size=2 * la + 1, mode="nearest")
    y = signal.resample_poly(up * g[:, None], 1, os, axis=0)[:len(x)]
    return y, float(20 * np.log10(g.min()))


# -------------------------------------------------------------- edits ----
def tape_stop(x, s0, n):
    """Decelerate x[s0:...] to a stop over n output samples (consumes n/2)."""
    out = x.copy()
    ph = s0 + np.cumsum(np.linspace(1.0, 0.0, n)) - 1.0
    i0 = np.floor(ph).astype(int)
    fr = (ph - i0)[:, None]
    seg = x[i0] * (1 - fr) + x[np.minimum(i0 + 1, len(x) - 1)] * fr
    fade = np.linspace(1, 0, n) ** 0.5
    out[s0:s0 + n] = seg * fade[:, None]
    return out


def reverse_swell(src, dst, start, length, sr, tmpdir):
    """Reverse `length` samples of src from `start`, gain-ramped, added to dst ending at start."""
    piece = None
    try:  # ComposerBench's own non-destructive slicer (reverse + dB envelope)
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from composerbench.production import resample_audio
        tmp = pathlib.Path(tmpdir) / "swell_src.wav"
        write_wav24(tmp, np.clip(src, -0.999, 0.999), sr)
        dur = length / sr
        rep = resample_audio(str(tmp), str(tmpdir), [{"start_seconds": start / sr,
                                                      "end_seconds": start / sr + dur, "reverse": True}],
                             gain_points=[{"seconds": 0.0, "db": -30.0}, {"seconds": dur * 0.7, "db": -8.0},
                                          {"seconds": dur, "db": 0.0}], fade_ms=2.0)
        piece, _ = read_wav(rep["file"])
        piece = piece[:length]
        via = "composerbench.production.resample_audio"
    except Exception as e:  # noqa: BLE001 - numpy fallback
        seg = src[start:start + length][::-1]
        ramp = 10 ** (np.interp(np.arange(length), [0, 0.7 * length, length], [-30, -8, 0]) / 20)
        piece = seg * ramp[:, None]
        via = f"numpy fallback ({type(e).__name__})"
    dst[start - len(piece):start] += piece
    return via


# ---------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(pathlib.Path.home() / "Music/ComposerBench/glitter_rush"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--target-lufs", type=float, default=-9.0)
    ap.add_argument("--ceiling", type=float, default=-1.0, help="true-peak ceiling dBTP")
    a = ap.parse_args()
    src = pathlib.Path(a.src).expanduser()
    out = pathlib.Path(a.out).expanduser() if a.out else src
    out.mkdir(parents=True, exist_ok=True)
    (out / "_tmp").mkdir(exist_ok=True)

    sc = json.loads((src / "score.json").read_text())
    bpm = sc["bpm"]
    spb = 60.0 / bpm
    stems, sr = {}, None
    for name in STEMS:
        stems[name], sr = read_wav(src / "stems" / f"{name}.wav")
    n = min(len(v) for v in stems.values())
    stems = {k: v[:n].copy() for k, v in stems.items()}
    B = lambda beat: int(round(beat * spb * sr))  # noqa: E731
    sec_of = {}
    for name, a0, b0 in sc["sections"]:
        for bar in range(a0, b0):
            sec_of[bar] = name
    section_at = lambda t: sec_of.get(int(t / spb // 4), "tail")  # noqa: E731
    report = {"sr": sr, "seconds": n / sr, "raw": {}, "gains_db": {}, "edits": []}

    # 1. stem balance from drop-1 loudness --------------------------------
    d0, d1 = B(32 * 4), B(48 * 4)
    target = {"drums": -12.5, "bass": -13.5, "synths": -13.5, "lead": -13.0, "fx": -24.0}
    for k in STEMS:
        l = lufs(stems[k], sr) if k == "fx" else lufs(stems[k][d0:d1], sr)
        report["raw"][k] = {"drop1_lufs": round(l, 2), "peak_db": round(20 * math.log10(np.abs(stems[k]).max() + 1e-12), 2)}
        g = target[k] - l if l > -60 else 0.0
        report["gains_db"][k] = round(g, 2)
        stems[k] *= 10 ** (g / 20)

    # 1b. stem tone: crisper hats/snare, wider supersaws ------------------------
    stems["drums"] = eq(stems["drums"], sr, [("peak", 3000, 1.0, 2.0), ("highshelf", 7000, 0.7, 3.0)])
    lo = signal.butter(2, 250, btype="high", fs=sr, output="sos")
    m_, s_w = (stems["synths"][:, 0] + stems["synths"][:, 1]) / 2, (stems["synths"][:, 0] - stems["synths"][:, 1]) / 2
    s_w = s_w + signal.sosfilt(lo, s_w) * (10 ** (3.0 / 20) - 1)
    stems["synths"] = np.stack([m_ + s_w, m_ - s_w], axis=1)

    # 1c. bar-level "automation" (MAGDA has no automation lanes yet) --------
    # dB offsets per stem; pre/build ramp up so the drop is the loudest moment
    def auto_db(stem, bar):
        sec = sec_of.get(bar, "tail")
        base = {
            "verse": {"drums": -2.5, "bass": -4.5, "synths": -1.0, "lead": 1.5},
            "drop1": {"synths": 1.5, "lead": 1.0, "bass": 0.5},
            "break": {"drums": -4.0, "bass": -4.0, "lead": 9.0},
            "drop2": {"synths": 2.5, "lead": 2.0, "drums": 1.0, "bass": 1.0},
        }.get(sec, {}).get(stem, 0.0)
        if sec in ("pre", "build"):
            a0 = 24 if sec == "pre" else 56
            length = 8 if sec == "pre" else 4
            ramp = -1.0 - 3.5 * (1 - (bar - a0) / (length - 1))   # -4.5 dB -> -1 dB
            extra = {"drums": -1.0, "bass": -1.5}.get(stem, 0.0) * (1 - (bar - a0) / (length - 1))
            base = ramp + extra
        return base
    bars_total = int(n / sr / (4 * spb)) + 1
    ctrl_t = np.array([B(bar * 4) for bar in range(bars_total + 1)])
    for k in STEMS:
        ctrl_db = np.array([auto_db(k, bar) for bar in range(bars_total + 1)])
        # step at section boundaries, linear inside ramps: sample per bar then smooth 30 ms
        curve_db = ctrl_db[np.minimum(np.arange(n) // max(1, B(4)), bars_total)]
        ramp_sections = [(24, 32), (56, 60)]
        for a0, b0 in ramp_sections:
            i0, i1 = B(a0 * 4), B(b0 * 4)
            curve_db[i0:i1] = np.interp(np.arange(i0, i1), ctrl_t[a0:b0 + 1],
                                        [auto_db(k, b) for b in range(a0, b0)] + [auto_db(k, b0 - 1)])
        curve_db = uniform_filter1d(curve_db, int(0.03 * sr))
        stems[k] *= (10 ** (curve_db / 20))[:, None]
    report["automation"] = "bar-level stem gains: verse/break pulled back, pre/build ramp -3 dB -> 0, drops lifted"

    # 2. MIDI-keyed sidechain ----------------------------------------------
    kicks = [nn["start"] * spb for nn in sc["parts"]["kick"]]
    depth = {"synths": {"drop1": 7.5, "drop2": 8.0, "pre": 4.0, "build": 5.0, "verse": 3.0, "break": 2.0, "outro": 4.0},
             "bass": {"drop1": 3.0, "drop2": 3.0, "pre": 2.0, "build": 2.0, "verse": 2.0},
             "lead": {"drop1": 2.0, "drop2": 2.5},
             "fx": {"drop1": 3.0, "drop2": 3.0}}
    rel = {"synths": 0.15, "bass": 0.09, "lead": 0.12, "fx": 0.15}
    for k, dmap in depth.items():
        curve = duck_curve(n, sr, kicks, lambda t, dm=dmap: dm.get(section_at(t), 0.0), release=rel[k])
        stems[k] *= curve[:, None]
    report["sidechain_kicks"] = len(kicks)

    # 3. glitch edits ---------------------------------------------------------
    for gap_beat in (127.0, 239.0):                     # reversed swell into the drops
        s = B(gap_beat + 1.0)
        via = reverse_swell(stems["synths"] + stems["lead"], stems["synths"], s, B(1.0), sr, out / "_tmp")
        report["edits"].append(f"reverse swell before beat {gap_beat + 1:.0f} via {via}")
    mix_parts = stems
    ts0, tsn = B(191.0), B(1.0)                         # tape stop into the break
    for k in STEMS:
        mix_parts[k] = tape_stop(mix_parts[k], ts0, tsn)
    report["edits"].append("tape stop beats 191-192 (end of drop 1)")

    report["stem_section_lufs"] = {
        k: {name: round(lufs(mix_parts[k][B(a0 * 4):B(b0 * 4)], sr), 1) for name, a0, b0 in sc["sections"]}
        for k in STEMS}
    mix = sum(mix_parts[k] for k in STEMS)
    i1, i2 = B(12.0), B(16.0)                           # intro: phone filter opening up by bar 4
    phone = signal.sosfilt(signal.butter(2, [350, 3800], btype="band", fs=sr, output="sos"), mix[:i2], axis=0)
    w = np.ones(i2)
    w[i1:i2] = np.linspace(1, 0, i2 - i1) ** 2
    mix[:i2] = phone * 1.25 * w[:, None] + mix[:i2] * (1 - w[:, None])
    report["edits"].append("band-limited 'phone' intro, crossfading to full range over bar 4")

    # 4. master bus -----------------------------------------------------------
    m, s_ = (mix[:, 0] + mix[:, 1]) / 2, (mix[:, 0] - mix[:, 1]) / 2
    s_ = signal.sosfilt(signal.butter(2, 120, btype="high", fs=sr, output="sos"), s_)
    mix = np.stack([m + s_, m - s_], axis=1)
    mix = eq(mix, sr, [("lowshelf", 55, 0.7, 0.5), ("peak", 320, 0.9, -1.5),
                       ("peak", 3800, 1.0, 1.5), ("highshelf", 9500, 0.7, 3.0)])
    mix, gr = glue(mix, sr, thresh_db=-12.0, ratio=2.0)
    report["glue_max_gr_db"] = round(gr, 2)

    def render(gain_db):
        y = soft_clip(mix * 10 ** (gain_db / 20), drive_db=1.5)
        y, lim = limit(y, sr, ceiling_db=a.ceiling - 0.3)
        tp = true_peak_db(y)
        if tp > a.ceiling:                               # resampling overshoot guard
            y *= 10 ** ((a.ceiling - 0.05 - tp) / 20)
        return y, lim

    g_lo, g_hi, best = -12.0, 18.0, None
    for _ in range(10):                                 # bisection on post-limiter loudness
        g = (g_lo + g_hi) / 2
        y, lim = render(g)
        L = lufs(y, sr)
        if best is None or abs(L - a.target_lufs) < abs(best[3] - a.target_lufs):
            best = (g, y, lim, L)
        if L > a.target_lufs:
            g_hi = g
        else:
            g_lo = g
        if abs(L - a.target_lufs) < 0.05:
            break
    g, y, lim, L = best
    tp = true_peak_db(y)
    # 10 ms fade-in, 2 s fade-out on the tail
    y[:int(0.01 * sr)] *= np.linspace(0, 1, int(0.01 * sr))[:, None]
    y[-2 * sr:] *= np.linspace(1, 0, 2 * sr)[:, None] ** 2
    report["master"] = {"gain_db": round(g, 2), "limiter_max_gr_db": round(lim, 2),
                        "integrated_lufs": round(L, 2), "true_peak_dbtp": round(tp, 2),
                        "short_term_lufs_1s": [round(v, 1) for v in short_term(y, sr)]}
    write_wav24(out / "glitter_rush_master.wav", y, sr)
    write_wav24(out / "glitter_rush_premaster.wav", mix * 0.5, sr)
    # MP3 gets 1 dB extra headroom: lossy encoding of a dense master adds ~1 dB of inter-sample peaks
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / "glitter_rush_master.wav"),
                    "-af", "volume=-1dB", "-codec:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3",
                    "-metadata", "title=Glitter Rush", "-metadata", "artist=ComposerBench x Claude",
                    str(out / "glitter_rush.mp3")], check=False)
    (out / "master_report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "master"}, indent=1))
    print(json.dumps({k: v for k, v in report["master"].items() if k != "short_term_lufs_1s"}, indent=1))


if __name__ == "__main__":
    main()
