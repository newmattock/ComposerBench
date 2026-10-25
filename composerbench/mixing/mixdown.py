"""Run a mix spec: stems in, master + report out.

    report = run_mix("mix.json")          # CLI: composerbench mix mix.json

Order of operations (each stage optional):

  per stem   EQ -> filter automation -> gain automation -> sidechain ducking
             -> pan / width / low mono -> stem-targeted edits
  sum        stems -> cuts (with stems that keep playing)
  bus        EQ -> glue compression -> low mono -> width -> mix edits
  master     drive -> soft clip -> loudness target (iterative) -> true-peak
             limiter -> fades / trim -> dithered export (+ MP3/FLAC via ffmpeg)

The report holds what was applied, gain reduction, the master's LUFS, true
peak and LRA, short-term loudness over time and a per-section breakdown of the
mix and every stem.
"""
from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import time
from pathlib import Path

import numpy as np

from composerbench.analysis.wav import read_wav
from composerbench.mixing import dsp
from composerbench.mixing.loudness import integrated_lufs, loudness_summary, short_term_lufs, true_peak_db
from composerbench.mixing.spec import Timeline, load_mix_spec


class MixError(ValueError):
    pass


def write_wav(path: Path, x: np.ndarray, sr: int, bits: int = 24) -> Path:
    x = np.clip(x, -1.0, 1.0 - 2 ** -23)
    ch = x.shape[1]
    if bits == 32:
        data = x.astype("<f4").tobytes()
        fmt = struct.pack("<HHIIHH", 3, ch, sr, sr * ch * 4, ch * 4, 32)
    elif bits == 24:
        q = np.round(dsp.tpdf_dither(x, 24) * 8388607.0).astype(np.int32).reshape(-1)
        b = np.empty((q.size, 3), dtype=np.uint8)
        b[:, 0], b[:, 1], b[:, 2] = q & 0xFF, (q >> 8) & 0xFF, (q >> 16) & 0xFF
        data = b.tobytes()
        fmt = struct.pack("<HHIIHH", 1, ch, sr, sr * ch * 3, ch * 3, 24)
    elif bits == 16:
        data = np.round(np.clip(dsp.tpdf_dither(x, 16), -1, 1 - 2 ** -15) * 32767.0).astype("<i2").tobytes()
        fmt = struct.pack("<HHIIHH", 1, ch, sr, sr * ch * 2, ch * 2, 16)
    else:
        raise ValueError("bits must be 16, 24 or 32")
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def _ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for cand in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(cand).exists():
            return cand
    return None


def run_mix(source, out_dir: str | Path | None = None, write_files: bool = True) -> dict:
    t_start = time.time()
    spec, base = load_mix_spec(source)
    out = Path(out_dir or spec.out_dir or base).expanduser()
    out = out if out.is_absolute() else (base / out)
    notes: list[str] = []
    applied: list[str] = []

    score = None
    if spec.score:
        sp = Path(spec.score).expanduser()
        sp = sp if sp.is_absolute() else base / sp
        if not sp.exists():
            raise MixError(f"score not found: {sp}")
        score = json.loads(sp.read_text())
    bpm = spec.bpm or (score or {}).get("bpm")
    sections = spec.sections if spec.sections is not None else (score or {}).get("sections", [])
    tl = Timeline(bpm, spec.time_unit, sections)

    # ------------------------------------------------------------ load stems --
    stems_dir = Path(spec.stems_dir).expanduser()
    stems_dir = stems_dir if stems_dir.is_absolute() else base / stems_dir
    names = list(spec.stems) or sorted(p.stem for p in stems_dir.glob("*.wav"))
    if not names:
        raise MixError(f"no stems: list them under 'stems' or put WAVs in {stems_dir}")
    audio: dict[str, np.ndarray] = {}
    sr = None
    for name in names:
        st = spec.stems.get(name)
        p = Path(st.path).expanduser() if st and st.path else stems_dir / f"{name}.wav"
        p = p if p.is_absolute() else base / p
        if not p.exists():
            raise MixError(f"stem {name!r} not found at {p}")
        x, rate = read_wav(p)
        if sr is None:
            sr = rate
        elif rate != sr:
            raise MixError(f"stem {name!r} is {rate} Hz but the others are {sr} Hz")
        audio[name] = dsp.as_stereo(x)
    n = max(len(x) for x in audio.values())
    for name, x in audio.items():
        st = spec.stems.get(name)
        off = int(round(tl.to_seconds(st.offset) * sr)) if st and st.offset else 0
        y = np.zeros((n + off, 2))
        y[off:off + len(x)] = x
        audio[name] = y
    n = max(len(x) for x in audio.values())
    audio = {k: np.pad(v, ((0, n - len(v)), (0, 0))) for k, v in audio.items()}
    raw_stats = {k: {"lufs": _r(integrated_lufs(v, sr)), "peak_dbfs": _r(_peak_db(v))} for k, v in audio.items()}

    # ---------------------------------------------------- sidechain triggers --
    duck = {name: np.ones(n) for name in audio}
    for i, sc in enumerate(spec.sidechain):
        times, vels = _trigger_times(sc.trigger, score, audio, sr, tl)
        if not len(times):
            notes.append(f"sidechain[{i}]: no triggers found")
            continue
        depths = []
        for t, v in zip(times, vels):
            d = sc.depth_db
            if sc.section_depth_db and bpm:
                beat = t * bpm / 60.0
                sec = next((s for s in sections if s["start"] <= beat < s["end"]), None)
                if sec and sec["name"] in sc.section_depth_db:
                    d = sc.section_depth_db[sec["name"]]
            depths.append(d * (v / 127.0 if sc.velocity_scaled else 1.0))
        env = dsp.duck_envelope(n, sr, times, depths, sc.attack_ms, sc.hold_ms, sc.release_ms)
        for target in sc.targets:
            if target not in duck:
                raise MixError(f"sidechain[{i}] targets unknown stem {target!r}; stems: {sorted(audio)}")
            duck[target] = np.minimum(duck[target], env)
        applied.append(f"sidechain[{i}]: {len(times)} triggers -> {sc.targets}")

    # -------------------------------------------------------- per-stem chain --
    for name in list(audio):
        st = spec.stems.get(name)
        x = audio[name]
        if st is not None and st.mute:
            audio[name] = np.zeros_like(x)
            continue
        if st is not None:
            if st.eq:
                x = dsp.eq(x, sr, [b.model_dump() for b in st.eq])
            a = st.automation
            if a.highpass_hz:
                x = dsp.filter_sweep(x, sr, "highpass", _pts(a.highpass_hz, tl), a.filter_q)
            if a.lowpass_hz:
                x = dsp.filter_sweep(x, sr, "lowpass", _pts(a.lowpass_hz, tl), a.filter_q)
            if a.gain_db:
                x = dsp.gain_automation(x, sr, _pts(a.gain_db, tl))
            if st.gain_db:
                x = x * 10 ** (st.gain_db / 20)
        if not np.all(duck[name] == 1.0):
            x = x * duck[name][:, None]
        if st is not None:
            if st.pan is not None:
                x = dsp.pan(x, st.pan)
            if st.width is not None:
                x = dsp.width(x, st.width)
            if st.mono_below_hz:
                x = dsp.mono_below(x, sr, st.mono_below_hz)
        audio[name] = x

    # ------------------------------------------------- stem-targeted edits --
    for i, e in enumerate(spec.edits):
        if e.stems == "mix":
            continue
        for name in e.stems:
            if name not in audio:
                raise MixError(f"edits[{i}] targets unknown stem {name!r}")
            audio[name] = _edit(e, audio[name], sr, tl)
        applied.append(f"edits[{i}] {e.type} on {e.stems}")

    # ----------------------------------------------------------- cuts + sum --
    for i, c in enumerate(spec.cuts):
        a, b = tl.to_seconds(c.start), tl.to_seconds(c.end)
        for name in audio:
            if name not in c.keep:
                audio[name] = dsp.mute_window(audio[name], sr, a, b, c.fade_ms)
        applied.append(f"cuts[{i}] {a:.3f}-{b:.3f}s (kept {c.keep or 'nothing'})")
    mix = sum(audio.values())

    # ------------------------------------------------------------------ bus --
    bus = spec.bus
    glue_gr = 0.0
    if bus.eq:
        mix = dsp.eq(mix, sr, [b.model_dump() for b in bus.eq])
    if bus.glue:
        g = bus.glue
        mix, glue_gr = dsp.compress(mix, sr, g.threshold_db, g.ratio, g.attack_ms, g.release_ms,
                                    g.knee_db, g.makeup_db)
    if bus.mono_below_hz:
        mix = dsp.mono_below(mix, sr, bus.mono_below_hz)
    if bus.width != 1.0:
        mix = dsp.width(mix, bus.width)

    # mix-level edits act on the bus output, like an effect on the master track
    for i, e in enumerate(spec.edits):
        if e.stems == "mix":
            mix = _edit(e, mix, sr, tl)
            applied.append(f"edits[{i}] {e.type} on the mix")
    premaster = mix.copy()

    # --------------------------------------------------------------- master --
    m = spec.master
    if m.drive_db:
        mix = mix * 10 ** (m.drive_db / 20)
    max_gain_db = 36.0
    gain_db = 0.0
    used_gain_db = 0.0
    limit_gr = 0.0
    ceiling = 10 ** (m.ceiling_dbtp / 20)
    work = mix
    def master_pass(g: float, oversample: int):
        y = work * 10 ** (g / 20)
        if m.soft_clip:
            y = dsp.soft_clip(y, ceiling * 1.12)
        return dsp.limit(y, sr, m.ceiling_dbtp - 0.05, m.lookahead_ms, m.release_ms, oversample)

    # converge on the loudness target with cheap sample-peak passes, then one
    # 4x-oversampled (true-peak) pass; a last correction if that moved it
    final = False
    for _ in range(8):
        used_gain_db = gain_db
        y, limit_gr = master_pass(gain_db, 4 if final else 1)
        if m.target_lufs is None:
            if not final:
                final = True
                continue
            break
        got = integrated_lufs(y, sr)
        if not math.isfinite(got):
            notes.append("master is silent; loudness target skipped")
            break
        err = m.target_lufs - got
        if abs(err) < 0.1:
            if final:
                break
            final = True
            continue
        nxt = min(max_gain_db, gain_db + err)
        if abs(nxt - gain_db) < 0.05:
            if not final:
                final = True
                continue
            notes.append(f"loudness target not reached: master gain capped at {max_gain_db} dB "
                         f"(the stems are very quiet)")
            break
        gain_db = nxt
    if not final:                            # loop ran out before the true-peak pass
        y, limit_gr = master_pass(used_gain_db, 4)
    gain_db = used_gain_db
    tp = true_peak_db(y)
    if tp > m.ceiling_dbtp:                  # tiny residual inter-sample overshoot: trim it
        y = y * 10 ** ((m.ceiling_dbtp - tp - 0.01) / 20)
    end_s = tl.to_seconds(m.end) if m.end is not None else None
    if end_s is not None:
        y = y[: int(end_s * sr)]
    if m.fade_in:
        k = int((tl.length_seconds(m.fade_in) or 0) * sr)
        if k > 1:
            y[:k] *= np.linspace(0, 1, k)[:, None] ** 2
    if m.fade_out:
        k = int((tl.length_seconds(m.fade_out) or 0) * sr)
        if k > 1:
            y[-k:] *= np.linspace(1, 0, k)[:, None] ** 2
    if np.abs(y).max(initial=0) > 1.0:
        raise MixError("master exceeds full scale after limiting (bug); refusing to write")

    # --------------------------------------------------------------- report --
    sec_s = tl.section_seconds()
    from composerbench.analysis.sections import section_report
    report = {
        "out_dir": str(out), "sample_rate": sr, "seconds": round(len(y) / sr, 3),
        "stems_in": raw_stats, "applied": applied, "notes": notes,
        "bus": {"glue_max_gr_db": round(glue_gr, 2)},
        "master": {**loudness_summary(y, sr), "gain_db": round(gain_db, 2),
                   "limiter_max_gr_db": round(limit_gr, 2), "target_lufs": m.target_lufs,
                   "ceiling_dbtp": m.ceiling_dbtp},
        "short_term_lufs_1s": [None if not math.isfinite(v) else round(float(v), 1)
                               for v in short_term_lufs(y, sr, 1.0)],
        "sections": section_report(y, sr, sec_s, {k: v for k, v in audio.items()}) if sec_s else None,
        "files": {}, "seconds_to_mix": None,
    }
    if write_files:
        out.mkdir(parents=True, exist_ok=True)
        wav = out / f"{spec.export.basename}.wav"
        write_wav(wav, y, sr, spec.export.bit_depth)
        report["files"]["wav"] = str(wav)
        ff = _ffmpeg()
        if (spec.export.mp3 or spec.export.flac) and ff is None:
            notes.append("ffmpeg not found: skipped MP3/FLAC")
        if ff and spec.export.mp3:
            mp3 = out / f"{spec.export.basename}.mp3"
            _run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libmp3lame",
                  "-b:a", spec.export.mp3_bitrate, str(mp3)], notes)
            if mp3.exists():
                report["files"]["mp3"] = str(mp3)
        if ff and spec.export.flac:
            flac = out / f"{spec.export.basename}.flac"
            _run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-sample_fmt", "s16",
                  str(flac)], notes)
            if flac.exists():
                report["files"]["flac"] = str(flac)
        if spec.export.spectrogram:
            png = _spectrogram(out / f"{spec.export.basename}_spectrogram.png", y, sr, sec_s, notes)
            if png:
                report["files"]["spectrogram"] = str(png)
        premaster_stats = {"lufs": _r(integrated_lufs(premaster, sr)), "peak_dbfs": _r(_peak_db(premaster))}
        report["premaster"] = premaster_stats
        report["seconds_to_mix"] = round(time.time() - t_start, 1)
        (out / f"{spec.export.basename}_report.json").write_text(json.dumps(report, indent=1, default=str))
        report["files"]["report"] = str(out / f"{spec.export.basename}_report.json")
    else:
        report["seconds_to_mix"] = round(time.time() - t_start, 1)
        report["_master_audio"] = y
    return report


def _r(v, d=2):
    return None if v is None or not math.isfinite(v) else round(float(v), d)


def _peak_db(x):
    p = float(np.abs(x).max(initial=0.0))
    return 20 * math.log10(p) if p > 0 else float("-inf")


def _pts(points, tl: Timeline) -> list[tuple[float, float]]:
    """Breakpoints in seconds; a "jump" point becomes a hold + step."""
    out: list[tuple[float, float]] = []
    for p in points:
        t, v = tl.to_seconds(p[0]), float(p[1])
        if len(p) > 2 and p[2] == "jump" and out:
            out.append((max(out[-1][0], t - 1e-4), out[-1][1]))
        out.append((t, v))
    return out


def _trigger_times(tr, score, audio, sr, tl: Timeline):
    if tr.times is not None:
        return np.array([tl.to_seconds(t) for t in tr.times]), np.full(len(tr.times), 127)
    if tr.part is not None:
        if not score or tr.part not in score.get("parts", {}):
            raise MixError(f"sidechain trigger part {tr.part!r} not in the score")
        spb = 60.0 / score["bpm"]
        notes = [n for n in score["parts"][tr.part] if n.get("velocity", 100) >= tr.min_velocity]
        return (np.array([n["start"] * spb for n in notes]),
                np.array([n.get("velocity", 100) for n in notes]))
    if tr.stem is not None:
        if tr.stem not in audio:
            raise MixError(f"sidechain trigger stem {tr.stem!r} not loaded")
        t = dsp.detect_onsets(audio[tr.stem], sr, tr.band_hz, tr.threshold_db)
        return t, np.full(len(t), 127)
    raise MixError("a trigger needs 'part', 'times' or 'stem'")


def _edit(e, x: np.ndarray, sr: int, tl: Timeline) -> np.ndarray:
    start = tl.to_seconds(e.start) if e.start is not None else 0.0
    if e.end is not None:
        end = tl.to_seconds(e.end)
    elif e.length is not None:
        end = start + tl.length_seconds(e.length)
    else:
        end = len(x) / sr
    if e.type == "tape_stop":
        return dsp.tape_stop(x, sr, start, end - start, e.curve, e.silence_after)
    if e.type == "stutter":
        if e.slice is None:
            raise MixError("stutter needs 'slice'")
        return dsp.stutter(x, sr, start, end - start, tl.length_seconds(e.slice), e.decay_db)
    if e.type == "reverse_swell":
        src = tl.to_seconds(e.source_start) if e.source_start is not None else start
        return dsp.reverse_swell(x, sr, src, end - start, end, e.gain_db, e.curve)
    if e.type in ("telephone", "bitcrush"):
        fade_s = tl.length_seconds(e.fade) or 0.01
        pad = fade_s + 0.25                       # process only the edited region (+ filter settling)
        a, b = max(0, int((start - pad) * sr)), min(len(x), int((end + fade_s) * sr) + 1)
        if b <= a:
            return x
        seg = x[a:b]
        wet = dsp.telephone(seg, sr, e.lo_hz, e.hi_hz) if e.type == "telephone" else \
            dsp.bitcrush(seg, sr, e.bits, e.rate_hz)
        y = x.copy()
        y[a:b] = dsp.crossfade_regions(seg, wet, sr, start - a / sr, end - a / sr, fade_s)
        return y
    if e.type == "gain":
        g = np.ones(len(x))
        a, b = int(start * sr), int(end * sr)
        g[a:b] = 10 ** (e.gain_db / 20)
        return x * g[:, None]
    if e.type == "mute":
        return dsp.mute_window(x, sr, start, end)
    raise MixError(f"unknown edit {e.type}")


def _run(cmd: list[str], notes: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except Exception as e:  # noqa: BLE001
        notes.append(f"{Path(cmd[0]).name} failed: {e}"[:300])


def _spectrogram(path: Path, y: np.ndarray, sr: int, sections: list[dict], notes: list[str]) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from scipy import signal
    except Exception:  # noqa: BLE001
        notes.append("matplotlib not available: no spectrogram")
        return None
    m = y.mean(axis=1)
    f, t, s = signal.spectrogram(m, sr, nperseg=2048, noverlap=1024)
    fig, ax = plt.subplots(2, 1, figsize=(14, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax[0].pcolormesh(t, f, 10 * np.log10(s + 1e-12), shading="auto", vmin=-110, vmax=-30, cmap="magma")
    ax[0].set_yscale("symlog", linthresh=200)
    ax[0].set_ylim(30, sr / 2)
    ax[0].set_ylabel("Hz")
    st = np.maximum(short_term_lufs(y, sr, 0.25), -60.0)
    ax[1].plot(np.arange(len(st)) * 0.25 + 1.5, st, lw=0.8)
    ax[1].set_ylabel("short-term LUFS")
    ax[1].set_xlabel("seconds")
    for s_ in sections:
        for a in ax:
            a.axvline(s_["start_s"], color="cyan", lw=0.7, alpha=0.7)
        ax[0].text(s_["start_s"] + 0.3, sr / 2 * 0.6, s_["name"], color="white", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return path
