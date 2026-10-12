"""Offline DSP for the mix/master stage (numpy + scipy).

Everything takes and returns float64 arrays shaped (frames, channels) and
works on whole stems at once, so a two-minute song mixes in seconds. Times
are in seconds here; the mix spec converts beats and section names first.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import signal
from scipy.ndimage import maximum_filter1d, minimum_filter1d


def db_to_gain(db):
    return np.power(10.0, np.asarray(db, dtype=np.float64) / 20.0)


def gain_to_db(g, floor=-120.0):
    g = np.asarray(g, dtype=np.float64)
    with np.errstate(divide="ignore"):
        return np.maximum(20.0 * np.log10(np.maximum(g, 1e-12)), floor)


def as_stereo(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return x[:, :2]


ANTI_DENORMAL = 1e-18      # about -360 dBFS: far below audibility, far above subnormal floats


def guarded(xt: np.ndarray) -> np.ndarray:
    """Add a -360 dB DC + Nyquist pattern to a (channels, frames) copy so IIR
    states never decay into subnormal floats. Filters ringing out into digital
    silence otherwise run ~30x slower on x86 (render silences are common)."""
    xt[..., ::2] += ANTI_DENORMAL
    return xt


def sosfilt(sos: np.ndarray, x: np.ndarray, zi: np.ndarray | None = None):
    """scipy sosfilt over (frames, channels) audio, run on a contiguous
    (channels, frames) copy (several times faster than filtering along axis 0),
    guarded against subnormal floats. ``zi`` uses scipy's axis=-1 layout:
    (sections, channels, 2)."""
    xt = guarded(np.array(np.asarray(x, dtype=np.float64).T, order="C", copy=True))
    if zi is None:
        return signal.sosfilt(sos, xt, axis=-1).T
    y, zf = signal.sosfilt(sos, xt, axis=-1, zi=zi)
    return y.T, zf


# ------------------------------------------------------------------ biquads --
def biquad(kind: str, freq: float, sr: float, q: float = 0.7071, gain_db: float = 0.0) -> np.ndarray:
    """One RBJ-cookbook biquad as an SOS row.

    kind: lowpass | highpass | bandpass | bell (peak) | lowshelf | highshelf | notch
    """
    freq = float(min(max(freq, 5.0), 0.49 * sr))
    a_ = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * freq / sr
    c, s = math.cos(w0), math.sin(w0)
    alpha = s / (2 * max(q, 1e-3))
    k = kind.lower()
    if k in ("lowpass", "lp"):
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif k in ("highpass", "hp"):
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif k in ("bandpass", "bp"):
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif k in ("notch",):
        b = [1.0, -2 * c, 1.0]
        a = [1 + alpha, -2 * c, 1 - alpha]
    elif k in ("bell", "peak", "peaking"):
        b = [1 + alpha * a_, -2 * c, 1 - alpha * a_]
        a = [1 + alpha / a_, -2 * c, 1 - alpha / a_]
    elif k in ("lowshelf", "low_shelf"):
        sq = 2 * math.sqrt(a_) * alpha
        b = [a_ * ((a_ + 1) - (a_ - 1) * c + sq), 2 * a_ * ((a_ - 1) - (a_ + 1) * c),
             a_ * ((a_ + 1) - (a_ - 1) * c - sq)]
        a = [(a_ + 1) + (a_ - 1) * c + sq, -2 * ((a_ - 1) + (a_ + 1) * c), (a_ + 1) + (a_ - 1) * c - sq]
    elif k in ("highshelf", "high_shelf"):
        sq = 2 * math.sqrt(a_) * alpha
        b = [a_ * ((a_ + 1) + (a_ - 1) * c + sq), -2 * a_ * ((a_ - 1) + (a_ + 1) * c),
             a_ * ((a_ + 1) + (a_ - 1) * c - sq)]
        a = [(a_ + 1) - (a_ - 1) * c + sq, 2 * ((a_ - 1) - (a_ + 1) * c), (a_ + 1) - (a_ - 1) * c - sq]
    else:
        raise ValueError(f"unknown filter type {kind!r}")
    return np.array([b[0] / a[0], b[1] / a[0], b[2] / a[0], 1.0, a[1] / a[0], a[2] / a[0]])


def eq(x: np.ndarray, sr: float, bands: list[dict]) -> np.ndarray:
    """Static EQ. Each band: {"type", "freq", "q"?, "gain_db"?, "order"?}.
    ``order`` (2/4/6/8) stacks lowpass/highpass sections into steeper slopes."""
    if not bands:
        return x
    rows = []
    for b in bands:
        kind = b.get("type", "bell")
        order = int(b.get("order", 2))
        n = max(1, order // 2) if kind.lower() in ("lowpass", "lp", "highpass", "hp") else 1
        for _ in range(n):
            rows.append(biquad(kind, float(b["freq"]), sr, float(b.get("q", 0.7071)),
                               float(b.get("gain_db", 0.0))))
    return sosfilt(np.array(rows), x)


def butter(x: np.ndarray, sr: float, kind: str, freq, order: int = 4) -> np.ndarray:
    sos = signal.butter(order, freq, btype=kind, fs=sr, output="sos")
    if np.asarray(x).ndim == 1:
        return signal.sosfilt(sos, x)
    return sosfilt(sos, x)


# --------------------------------------------------------------- automation --
def curve(points: list[tuple[float, float]], n: int, sr: float, log: bool = False) -> np.ndarray:
    """Per-sample breakpoint curve (seconds, value); linear, or in the log domain
    for frequencies. Values hold before the first and after the last point."""
    pts = sorted((float(t), float(v)) for t, v in points)
    t = np.arange(n) / sr
    ts = np.array([p[0] for p in pts])
    vs = np.array([p[1] for p in pts])
    if log:
        return np.exp(np.interp(t, ts, np.log(np.maximum(vs, 1e-6))))
    return np.interp(t, ts, vs)


def gain_automation(x: np.ndarray, sr: float, points_db: list[tuple[float, float]]) -> np.ndarray:
    if not points_db:
        return x
    return x * db_to_gain(curve(points_db, len(x), sr))[:, None]


def filter_sweep(x: np.ndarray, sr: float, kind: str, points_hz: list[tuple[float, float]],
                 q: float = 0.7071, block: int = 512) -> np.ndarray:
    """Time-varying lowpass/highpass: cutoff log-interpolated between points,
    coefficients updated every ``block`` samples with the filter state carried
    across, stretches at a constant cutoff filtered in one pass, and regions
    where the filter is wide open passed through untouched."""
    if not points_hz:
        return x
    n = len(x)
    lp = kind.lower() in ("lowpass", "lp")
    starts = np.arange(0, n, block)
    t_mid = (starts + np.minimum(block, n - starts) / 2) / sr
    pts = sorted((float(t), float(v)) for t, v in points_hz)
    f_blocks = np.exp(np.interp(t_mid, [p[0] for p in pts], np.log(np.maximum([p[1] for p in pts], 1e-6))))
    open_blocks = f_blocks >= min(0.45 * sr, 19500.0) if lp else f_blocks <= 20.0
    if open_blocks.all():
        return x
    y = x.copy()
    ch = x.shape[1]
    zi = np.zeros((1, ch, 2))
    active = False
    i = 0
    nb = len(starts)
    while i < nb:
        a = int(starts[i])
        if open_blocks[i]:
            j = i
            while j + 1 < nb and open_blocks[j + 1]:
                j += 1
            b = min(n, int(starts[j]) + block)
            if active:                  # let the filter ring out over one block, then bypass
                e = min(b, a + block)
                seg, _ = sosfilt(biquad(kind, float(f_blocks[i]), sr, q)[None, :], x[a:e], zi)
                fade_in = np.linspace(0.0, 1.0, e - a)[:, None]
                y[a:e] = seg * (1 - fade_in) + x[a:e] * fade_in
                zi[:] = 0.0
                active = False
            i = j + 1
            continue
        j = i                            # extend over blocks with (nearly) the same cutoff
        while (j + 1 < nb and not open_blocks[j + 1]
               and abs(f_blocks[j + 1] / f_blocks[i] - 1.0) < 0.005):
            j += 1
        b = min(n, int(starts[j]) + block)
        sos = biquad(kind, float(f_blocks[i]), sr, q)[None, :]
        y[a:b], zi = sosfilt(sos, x[a:b], zi)
        active = True
        i = j + 1
    return y


# ---------------------------------------------------------------- dynamics --
def duck_envelope(n: int, sr: float, times_s, depths_db, attack_ms: float = 3.0, hold_ms: float = 10.0,
                  release_ms: float = 140.0) -> np.ndarray:
    """Gain curve that dips at each trigger (sidechain pumping keyed from the score).

    depths_db: one value per trigger (dB of reduction, positive) or a scalar.
    Overlapping dips take the deepest reduction.
    """
    times = np.asarray(list(times_s), dtype=np.float64)
    depths = np.broadcast_to(np.asarray(depths_db, dtype=np.float64), times.shape)
    red = np.zeros(n)                         # reduction in dB (>= 0)
    a = max(1, int(attack_ms * sr / 1000))
    h = int(hold_ms * sr / 1000)
    rel = max(1, int(release_ms * sr / 1000))
    tail = int(rel * 5)
    shape = np.concatenate([np.linspace(0.0, 1.0, a, endpoint=False), np.ones(h),
                            np.exp(-np.arange(tail) / rel)])
    for t, d in zip(times, depths):
        if d <= 0:
            continue
        i = int(round(t * sr)) - a
        lo, hi = max(0, i), min(n, i + len(shape))
        if hi <= lo:
            continue
        seg = shape[lo - i:hi - i] * d
        np.maximum(red[lo:hi], seg, out=red[lo:hi])
    return db_to_gain(-red)


def detect_onsets(x: np.ndarray, sr: float, band=(30.0, 150.0), threshold_db: float = -24.0,
                  min_gap_s: float = 0.09) -> np.ndarray:
    """Onset times (s) of a stem in a band, for sidechain keyed from audio."""
    m = as_stereo(x).mean(axis=1)
    y = butter(m, sr, "bandpass", list(band), order=2)
    env = maximum_filter1d(np.abs(y), size=max(1, int(0.002 * sr)))
    level = gain_to_db(env / max(env.max(), 1e-12))
    above = level > threshold_db
    rises = np.flatnonzero(above[1:] & ~above[:-1]) + 1
    out, last = [], -1e9
    for i in rises:
        t = i / sr
        if t - last >= min_gap_s:
            out.append(t)
            last = t
    return np.array(out)


def compress(x: np.ndarray, sr: float, threshold_db: float = -16.0, ratio: float = 2.0,
             attack_ms: float = 10.0, release_ms: float = 150.0, knee_db: float = 6.0,
             makeup_db: float = 0.0, rms_ms: float = 10.0) -> tuple[np.ndarray, float]:
    """Stereo-linked feed-forward compressor; returns (audio, max gain reduction dB)."""
    power = np.mean(x ** 2, axis=1)
    w = max(1, int(rms_ms * sr / 1000))
    level = 10 * np.log10(np.maximum(np.convolve(power, np.ones(w) / w, mode="same"), 1e-12))
    over = level - threshold_db
    gr = np.where(over <= -knee_db / 2, 0.0,
                  np.where(over >= knee_db / 2, over * (1 - 1 / ratio),
                           (1 - 1 / ratio) * (over + knee_db / 2) ** 2 / (2 * max(knee_db, 1e-9))))
    # ballistics at a 16-sample control rate: attack while reduction grows, release while it falls
    hop = 16
    target = gr[::hop]
    att = math.exp(-hop / max(1.0, attack_ms * sr / 1000))
    rel = math.exp(-hop / max(1.0, release_ms * sr / 1000))
    out = np.empty_like(target)
    y = 0.0
    for i, g in enumerate(target.tolist()):
        c = att if g > y else rel
        y = c * y + (1.0 - c) * g
        out[i] = y
    gr = np.interp(np.arange(len(x)), np.arange(len(out)) * hop, out)
    return x * db_to_gain(-gr + makeup_db)[:, None], float(gr.max(initial=0.0))


def soft_clip(x: np.ndarray, ceiling: float = 1.0) -> np.ndarray:
    """Transparent below about -6 dB of ``ceiling``, smoothly saturating above."""
    return ceiling * np.tanh(x / ceiling)


def limit(x: np.ndarray, sr: float, ceiling_db: float = -1.0, lookahead_ms: float = 1.5,
          release_ms: float = 60.0, oversample: int = 4) -> tuple[np.ndarray, float]:
    """Look-ahead brickwall limiter on inter-sample peaks; returns (audio, max GR dB).

    The required gain is computed from oversampled peaks, min-filtered forward
    over the look-ahead and averaged backward over the same window, so the
    applied gain is never above what any peak needs (no overshoot), then held
    with a one-pole release.
    """
    ceiling = 10 ** (ceiling_db / 20)
    n = len(x)
    if oversample > 1:
        up = signal.resample_poly(x, oversample, 1, axis=0)[: n * oversample]
        peaks = np.abs(up).reshape(n, oversample, x.shape[1]).max(axis=(1, 2))
    else:
        peaks = np.abs(x).max(axis=1)
    need = np.minimum(1.0, ceiling / np.maximum(peaks, 1e-12))
    la = max(1, int(lookahead_ms * sr / 1000))
    # forward-looking minimum over [t, t + la)
    fwd = minimum_filter1d(need, size=la, origin=-(la // 2), mode="nearest")
    # backward moving average over (t - la, t]
    c = np.concatenate([np.zeros(1), np.cumsum(fwd)])
    idx = np.arange(n)
    lo = np.maximum(0, idx - la + 1)
    avg = (c[idx + 1] - c[lo]) / (idx + 1 - lo)
    red = 1.0 - avg
    coef = math.exp(-1.0 / max(1.0, release_ms * sr / 1000))
    held = signal.lfilter([1 - coef], [1, -coef], maximum_filter1d(red, size=la) + ANTI_DENORMAL)
    gain = np.minimum(avg, 1.0 - np.minimum(held, 1.0))
    y = x * gain[:, None]
    return y, float(-gain_to_db(gain.min(initial=1.0)))


# ------------------------------------------------------------------ stereo --
def width(x: np.ndarray, amount: float) -> np.ndarray:
    """Mid/side width: 0 = mono, 1 = unchanged, >1 wider."""
    mid = (x[:, 0] + x[:, 1]) / 2
    side = (x[:, 0] - x[:, 1]) / 2 * amount
    return np.stack([mid + side, mid - side], axis=1)


def mono_below(x: np.ndarray, sr: float, freq: float) -> np.ndarray:
    """Remove the side signal below ``freq`` (tight, mono-safe low end)."""
    mid = (x[:, 0] + x[:, 1]) / 2
    side = (x[:, 0] - x[:, 1]) / 2
    side = butter(side, sr, "highpass", freq, order=4)
    return np.stack([mid + side, mid - side], axis=1)


def pan(x: np.ndarray, position: float) -> np.ndarray:
    """Constant-power balance, -1 (left) .. 1 (right)."""
    ang = (position + 1) * math.pi / 4
    return x * np.array([math.cos(ang), math.sin(ang)]) * math.sqrt(2)


# ------------------------------------------------------------------- edits --
def fade(n: int, sr: float, ms: float) -> np.ndarray:
    k = max(1, int(ms * sr / 1000))
    return np.linspace(0.0, 1.0, min(k, n))


def mute_window(x: np.ndarray, sr: float, start_s: float, end_s: float, fade_ms: float = 4.0) -> np.ndarray:
    """Silence [start, end) with short fades either side (cuts before drops)."""
    y = x.copy()
    a, b = int(round(start_s * sr)), int(round(end_s * sr))
    a, b = max(0, a), min(len(y), b)
    if b <= a:
        return y
    f = fade(len(y), sr, fade_ms)
    k = len(f)
    lo = max(0, a - k)
    y[lo:a] *= f[::-1][-(a - lo):, None] if a > lo else 1.0
    y[a:b] = 0.0
    hi = min(len(y), b + k)
    y[b:hi] *= f[: hi - b, None]
    return y


def tape_stop(x: np.ndarray, sr: float, start_s: float, dur_s: float, curve_power: float = 1.6,
              silence_after: bool = False) -> np.ndarray:
    """Slow the audio from ``start_s`` to a halt over ``dur_s`` (pitch and speed
    drop together, like a turntable or tape). After the stop the original
    timeline resumes, unless ``silence_after``."""
    y = x.copy()
    s0, n = int(round(start_s * sr)), int(round(dur_s * sr))
    if n <= 1 or s0 >= len(x):
        return y
    n = min(n, len(x) - s0)
    u = np.arange(n) / n
    speed = (1.0 - u) ** curve_power
    pos = s0 + np.concatenate([[0.0], np.cumsum(speed[:-1])])
    for ch in range(x.shape[1]):
        y[s0:s0 + n, ch] = np.interp(pos, np.arange(len(x)), x[:, ch])
    tail = fade(n, sr, 8.0)[::-1]
    y[s0 + n - len(tail):s0 + n] *= tail[:, None]
    if silence_after:
        y[s0 + n:] = 0.0
    return y


def stutter(x: np.ndarray, sr: float, start_s: float, length_s: float, slice_s: float,
            decay_db: float = 0.0, xfade_ms: float = 3.0) -> np.ndarray:
    """Repeat the first ``slice_s`` of the window across ``length_s`` (beat-repeat)."""
    y = x.copy()
    s0, n, k = int(round(start_s * sr)), int(round(length_s * sr)), int(round(slice_s * sr))
    if k < 8 or s0 >= len(x):
        return y
    piece = x[s0:s0 + k].copy()
    f = fade(k, sr, xfade_ms)
    piece[: len(f)] *= f[:, None]
    piece[-len(f):] *= f[::-1][:, None]
    reps = int(math.ceil(n / k))
    for r in range(reps):
        a = s0 + r * k
        b = min(len(y), a + k, s0 + n)
        if b <= a:
            break
        y[a:b] = piece[: b - a] * 10 ** (-decay_db * r / 20)
    return y


def reverse_swell(x: np.ndarray, sr: float, source_start_s: float, length_s: float, end_s: float,
                  gain_db: float = -6.0, curve_power: float = 2.0) -> np.ndarray:
    """Add a reversed copy of [source_start, +length) that swells up and ends
    exactly at ``end_s`` (the classic reverse cymbal/reverb into a drop)."""
    n = int(round(length_s * sr))
    s = int(round(source_start_s * sr))
    seg = x[s:s + n][::-1].copy()
    if not len(seg):
        return x
    env = (np.arange(len(seg)) / len(seg)) ** curve_power
    seg *= (env * 10 ** (gain_db / 20))[:, None]
    e = int(round(end_s * sr))
    a = e - len(seg)
    y = x.copy()
    lo = max(0, a)
    y[lo:e] += seg[lo - a:]
    return y


def telephone(x: np.ndarray, sr: float, lo_hz: float = 400.0, hi_hz: float = 3200.0,
              drive: float = 1.5) -> np.ndarray:
    y = butter(x, sr, "bandpass", [lo_hz, hi_hz], order=4)
    return np.tanh(y * drive) / math.tanh(drive)


def bitcrush(x: np.ndarray, sr: float, bits: int = 8, rate_hz: float | None = None) -> np.ndarray:
    y = x
    if rate_hz and rate_hz < sr:
        step = max(1, int(round(sr / rate_hz)))
        idx = (np.arange(len(x)) // step) * step
        y = x[idx]
    q = 2 ** (bits - 1)
    return np.round(y * q) / q


def crossfade_regions(dry: np.ndarray, wet: np.ndarray, sr: float, start_s: float, end_s: float,
                      fade_s: float = 0.05) -> np.ndarray:
    """``wet`` inside [start, end), ``dry`` outside, with linear crossfades."""
    n = len(dry)
    t = np.arange(n) / sr
    f = max(fade_s, 1e-4)
    m = np.clip(np.minimum((t - start_s) / f + 1, (end_s - t) / f + 1), 0, 1)
    m = np.where((t >= start_s - f) & (t < end_s + f), np.clip(m, 0, 1), 0.0)
    return dry * (1 - m)[:, None] + wet * m[:, None]


def tpdf_dither(x: np.ndarray, bits: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    lsb = 1.0 / (2 ** (bits - 1))
    return x + (rng.random(x.shape) - rng.random(x.shape)) * lsb
