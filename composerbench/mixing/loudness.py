"""Loudness and true peak per ITU-R BS.1770-4 / EBU R128 (numpy + scipy).

Works at any sample rate: the K-weighting shelf and high-pass are designed
from their analog prototypes (the same construction pyloudnorm uses), which
reproduces the standard's 48 kHz coefficients. Channel weights are 1.0
(mono/stereo). Gating: 400 ms blocks, 75 % overlap, -70 LUFS absolute gate,
-10 LU relative gate. LRA follows EBU Tech 3342 (3 s windows, -20 LU gate,
10th to 95th percentile).
"""
from __future__ import annotations

import math

import numpy as np
from scipy import signal


def _k_sos(sr: float) -> np.ndarray:
    """K-weighting as two biquads for any rate (Brecht De Man's derivation of the
    BS.1770 prototypes; reproduces the standard's 48 kHz coefficients)."""
    # stage 1: high shelf (+4 dB above ~1.7 kHz)
    g, q, fc = 3.999843853973347, 0.7071752369554196, 1681.974450955533
    k = math.tan(math.pi * fc / sr)
    vh = 10 ** (g / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0,
             1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    # stage 2: RLB high-pass (~38 Hz)
    q, fc = 0.5003270373238773, 38.13547087602444
    k = math.tan(math.pi * fc / sr)
    a0 = 1 + k / q + k * k
    hp = [1.0, -2.0, 1.0, 1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    return np.array([shelf, hp])


def _as2d(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return x[:, None] if x.ndim == 1 else x


def k_weight(x: np.ndarray, sr: float) -> np.ndarray:
    xt = np.array(_as2d(x).T, order="C", copy=True)
    xt[..., ::2] += 1e-18          # keeps filter states out of subnormal floats (~-360 dB)
    return signal.sosfilt(_k_sos(sr), xt, axis=-1).T


def _window_power(xk: np.ndarray, sr: float, window_s: float, hop_s: float) -> np.ndarray:
    """Mean-square per window, summed over channels (channel weights 1)."""
    n = len(xk)
    win = int(round(window_s * sr))
    hop = max(1, int(round(hop_s * sr)))
    if n < win:
        return np.zeros(0)
    csum = np.concatenate([np.zeros((1, xk.shape[1])), np.cumsum(xk ** 2, axis=0)])
    starts = np.arange(0, n - win + 1, hop)
    return ((csum[starts + win] - csum[starts]) / win).sum(axis=1)


def _lufs(power: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return -0.691 + 10 * np.log10(np.maximum(power, 1e-30))


def integrated_lufs(x: np.ndarray, sr: float) -> float:
    """Gated integrated loudness in LUFS (-inf for silence or < 400 ms)."""
    return integrated_lufs_k(k_weight(x, sr), sr)


def integrated_lufs_k(xk: np.ndarray, sr: float) -> float:
    """Integrated loudness of audio that is already K-weighted (lets callers
    filter a whole song once and measure many sections of it)."""
    p = _window_power(xk, sr, 0.4, 0.1)
    if not p.size:
        return float("-inf")
    l = _lufs(p)
    p = p[l > -70.0]
    if not p.size:
        return float("-inf")
    rel = _lufs(np.array([p.mean()]))[0] - 10.0
    gated = p[_lufs(p) > rel]
    return float(_lufs(np.array([gated.mean()]))[0]) if gated.size else float("-inf")


def short_term_lufs(x: np.ndarray, sr: float, hop_s: float = 1.0) -> np.ndarray:
    """3-second short-term loudness every ``hop_s`` seconds (LUFS)."""
    return _lufs(_window_power(k_weight(x, sr), sr, 3.0, hop_s))


def momentary_lufs(x: np.ndarray, sr: float, hop_s: float = 0.1) -> np.ndarray:
    return _lufs(_window_power(k_weight(x, sr), sr, 0.4, hop_s))


def loudness_range(x: np.ndarray, sr: float) -> float | None:
    """EBU Tech 3342 LRA in LU (None when there is too little gated material)."""
    st = short_term_lufs(x, sr, hop_s=0.1)
    st = st[st > -70.0]
    if st.size < 10:
        return None
    power = 10 ** ((st + 0.691) / 10)
    rel = -0.691 + 10 * math.log10(power.mean()) - 20.0
    st = st[st > rel]
    if st.size < 10:
        return None
    return float(np.percentile(st, 95) - np.percentile(st, 10))


def true_peak_linear(x: np.ndarray, oversample: int = 4, chunk_s: float = 10.0, sr: float = 44100) -> float:
    """Inter-sample peak via polyphase oversampling, processed in chunks."""
    x = _as2d(x)
    n = len(x)
    if n == 0:
        return 0.0
    step = int(chunk_s * sr)
    pad = 64
    peak = 0.0
    for start in range(0, n, step):
        a, b = max(0, start - pad), min(n, start + step + pad)
        up = signal.resample_poly(x[a:b], oversample, 1, axis=0)
        lo = (start - a) * oversample
        hi = lo + (min(n, start + step) - start) * oversample
        peak = max(peak, float(np.abs(up[lo:hi]).max(initial=0.0)))
    return max(peak, float(np.abs(x).max()))


def true_peak_db(x: np.ndarray, oversample: int = 4) -> float:
    tp = true_peak_linear(x, oversample)
    return 20 * math.log10(tp) if tp > 0 else float("-inf")


def loudness_summary(x: np.ndarray, sr: float) -> dict:
    x = _as2d(x)
    peak = float(np.abs(x).max(initial=0.0))
    rms = float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0
    st = short_term_lufs(x, sr, 1.0)
    lra = loudness_range(x, sr)
    il = integrated_lufs(x, sr)
    tp = true_peak_db(x)

    def r(v, d=2):
        return None if v is None or not math.isfinite(v) else round(float(v), d)
    return {"integrated_lufs": r(il), "true_peak_dbtp": r(tp),
            "sample_peak_dbfs": r(20 * math.log10(peak)) if peak > 0 else None,
            "rms_dbfs": r(20 * math.log10(rms)) if rms > 0 else None,
            "loudness_range_lu": r(lra, 1),
            "short_term_max_lufs": r(st.max()) if st.size else None,
            "seconds": round(len(x) / sr, 3)}
