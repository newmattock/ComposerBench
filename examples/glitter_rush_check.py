"""Quick QA for GLITTER RUSH renders: per-section loudness/brightness/width per
stem, onset-vs-score alignment for the kick, clipping, and a spectrogram PNG.
"""
import json
import math
import pathlib
import sys

import numpy as np
from scipy import signal

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from glitter_rush_master import STEMS, lufs, read_wav, true_peak_db  # noqa: E402


def band(x, sr, lo, hi):
    sos = signal.butter(4, [lo, hi], btype="band", fs=sr, output="sos")
    y = signal.sosfilt(sos, x.mean(axis=1))
    return 10 * math.log10(np.mean(y ** 2) + 1e-12)


def centroid(x, sr):
    m = x.mean(axis=1)
    f, p = signal.welch(m, sr, nperseg=4096)
    return float((f * p).sum() / max(p.sum(), 1e-20))


def width(x):
    mid, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2
    return float(np.sqrt(np.mean(side ** 2)) / max(np.sqrt(np.mean(mid ** 2)), 1e-12))


def main(src):
    src = pathlib.Path(src).expanduser()
    sc = json.loads((src / "score.json").read_text())
    spb = 60 / sc["bpm"]
    out = {}
    files = {"mix": src / "mix_raw.wav", **{s: src / "stems" / f"{s}.wav" for s in STEMS}}
    if (src / "glitter_rush_master.wav").exists():
        files["master"] = src / "glitter_rush_master.wav"
    if (src / "out" / "glitter_rush_master.wav").exists():
        files["master"] = src / "out" / "glitter_rush_master.wav"
    data = {}
    for k, p in files.items():
        if p.exists():
            data[k], sr = read_wav(p)
    for k, x in data.items():
        row = {"seconds": round(len(x) / sr, 2), "peak_db": round(20 * math.log10(np.abs(x).max() + 1e-12), 2),
               "clipped_samples": int((np.abs(x) >= 0.9999).sum()), "lufs": round(lufs(x, sr), 2),
               "sections": {}}
        for name, a, b in sc["sections"]:
            seg = x[int(a * 4 * spb * sr):int(b * 4 * spb * sr)]
            if len(seg) < sr:
                continue
            row["sections"][name] = {"lufs": round(lufs(seg, sr), 1), "cent": round(centroid(seg, sr)),
                                     "width": round(width(seg), 3),
                                     "sub": round(band(seg, sr, 25, 60), 1),
                                     "low": round(band(seg, sr, 60, 250), 1),
                                     "mid": round(band(seg, sr, 250, 2500), 1),
                                     "high": round(band(seg, sr, 2500, 16000), 1)}
        out[k] = row
    # kick onset alignment: first threshold crossing of the click band near each scored kick
    if "drums" in data:
        x = data["drums"].mean(axis=1)
        y = np.abs(signal.sosfilt(signal.butter(4, 2000, btype="high", fs=sr, output="sos"), x))
        errs = []
        for n in sc["parts"]["kick"]:
            t = n["start"] * spb
            i0, i1 = int((t - 0.01) * sr), int((t + 0.05) * sr)
            if i0 < 0 or i1 > len(y) or y[i0:i1].max() < 1e-5:
                continue
            seg = y[i0:i1]
            errs.append((i0 + int(np.argmax(seg > 0.2 * seg.max()))) / sr * 1000 - t * 1000)
        out["kick_onset_ms"] = {"n": len(errs), "median": round(float(np.median(errs)), 2),
                                "p90_abs": round(float(np.percentile(np.abs(errs), 90)), 2)}
    if "master" in data:
        out["master_true_peak"] = round(true_peak_db(data["master"]), 2)
    (src / "check_report.json").write_text(json.dumps(out, indent=1))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        x = data.get("master", data.get("mix"))
        m = x.mean(axis=1)
        f, t, S = signal.spectrogram(m, sr, nperseg=2048, noverlap=1024)
        fig, ax = plt.subplots(2, 1, figsize=(14, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        ax[0].pcolormesh(t, f, 10 * np.log10(S + 1e-12), shading="auto", vmin=-110, vmax=-30, cmap="magma")
        ax[0].set_yscale("symlog", linthresh=200)
        ax[0].set_ylim(30, 20000)
        ax[0].set_ylabel("Hz")
        for name, a, b in sc["sections"]:
            for axx in ax:
                axx.axvline(a * 4 * spb, color="cyan", lw=0.8, alpha=0.7)
            ax[0].text(a * 4 * spb + 0.5, 15000, name, color="white", fontsize=9)
        hop = int(0.1 * sr)
        rms = [20 * math.log10(np.sqrt(np.mean(m[i:i + hop] ** 2)) + 1e-9) for i in range(0, len(m) - hop, hop)]
        ax[1].plot(np.arange(len(rms)) * 0.1, rms, lw=0.7)
        ax[1].set_ylabel("RMS dBFS")
        ax[1].set_xlabel("seconds")
        fig.tight_layout()
        fig.savefig(src / "check_spectrogram.png", dpi=90)
    except Exception as e:  # noqa: BLE001
        out["plot_error"] = str(e)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "~/Music/ComposerBench/glitter_rush")
