"""Loudness metering, DSP invariants and a full mix-spec run on synthetic stems."""
import json
import math

import numpy as np
import pytest

from composerbench.analysis.wav import read_wav
from composerbench.mixing import dsp
from composerbench.mixing.loudness import _k_sos, integrated_lufs, loudness_range, short_term_lufs, true_peak_db
from composerbench.mixing.mixdown import MixError, run_mix, write_wav
from composerbench.mixing.spec import Timeline


def sine(f, seconds, sr=48000, amp=1.0, channels=2):
    t = np.arange(int(seconds * sr)) / sr
    s = amp * np.sin(2 * np.pi * f * t)
    return np.stack([s] * channels, axis=1) if channels > 1 else s[:, None]


def test_k_weighting_matches_bs1770_coefficients_at_48k():
    sos = _k_sos(48000)
    assert np.allclose(sos[0], [1.53512485958697, -2.69169618940638, 1.19839281085285, 1,
                                -1.69065929318241, 0.73248077421585], atol=1e-9)
    assert np.allclose(sos[1], [1, -2, 1, 1, -1.99004745483398, 0.99007225036621], atol=1e-9)


def test_reference_loudness_values():
    assert integrated_lufs(sine(997, 10), 48000) == pytest.approx(0.0, abs=0.05)
    left = sine(997, 10)
    left[:, 1] = 0
    assert integrated_lufs(left, 48000) == pytest.approx(-3.01, abs=0.05)
    assert integrated_lufs(sine(997, 10, amp=0.1), 48000) == pytest.approx(-20.0, abs=0.05)
    assert integrated_lufs(np.zeros((48000 * 5, 2)), 48000) == -math.inf
    # gating: 10 s loud + 10 s 30 dB quieter integrates to (almost) the loud part
    x = np.concatenate([sine(997, 10, amp=0.5), sine(997, 10, amp=0.5 * 10 ** (-30 / 20))])
    assert integrated_lufs(x, 48000) == pytest.approx(integrated_lufs(sine(997, 10, amp=0.5), 48000), abs=0.2)
    # LRA: a 30 dB drop falls under the -20 LU relative gate; a 10 dB step does not
    assert loudness_range(x, 48000) < 6
    step = np.concatenate([sine(997, 10, amp=0.5), sine(997, 10, amp=0.5 * 10 ** (-10 / 20))])
    assert loudness_range(step, 48000) == pytest.approx(10, abs=1.0)
    assert len(short_term_lufs(sine(997, 10), 48000, 1.0)) == 8


def test_true_peak_sees_intersample_peaks():
    sr = 44100
    t = np.arange(sr) / sr
    s = np.sin(2 * np.pi * (sr / 4) * t + np.pi / 4)        # samples land at +-0.707
    x = np.stack([s, s], 1)
    assert 20 * np.log10(np.abs(x).max()) == pytest.approx(-3.01, abs=0.05)
    assert true_peak_db(x) > -0.5


def test_limiter_respects_true_peak_ceiling():
    rng = np.random.default_rng(1)
    x = rng.standard_normal((44100 * 5, 2)) * 0.5
    y, gr = dsp.limit(x, 44100, -1.0)
    assert gr > 3
    assert true_peak_db(y) <= -0.9          # mixdown trims any residual 0.05 dB itself


def test_ducking_filter_sweep_and_edits():
    sr = 44100
    env = dsp.duck_envelope(sr * 2, sr, [0.5, 1.0], [6.0, 12.0], 3, 10, 100)
    db = 20 * np.log10(env)
    assert db[int(0.5 * sr)] == pytest.approx(-6, abs=0.05) and db[int(1.0 * sr)] == pytest.approx(-12, abs=0.05)
    assert db[int(0.2 * sr)] == 0 and db[-1] > -0.01
    noise = np.random.default_rng(0).standard_normal((sr * 4, 2)) * 0.2
    swept = dsp.filter_sweep(noise, sr, "lowpass", [(0, 300), (4, 20000)])
    early, late = swept[: sr // 2], swept[-sr // 2:]
    from scipy import signal
    f, p1 = signal.welch(early[:, 0], sr, nperseg=2048)
    _, p2 = signal.welch(late[:, 0], sr, nperseg=2048)
    assert p1[f > 3000].sum() / p1.sum() < 0.01 < p2[f > 3000].sum() / p2.sum()
    cut = dsp.mute_window(noise, sr, 1.0, 1.5)
    assert np.abs(cut[int(1.01 * sr):int(1.49 * sr)]).max() == 0
    stop = dsp.tape_stop(noise, sr, 1.0, 1.0, silence_after=True)
    assert np.abs(stop[int(2.1 * sr):]).max() == 0 and np.abs(stop[: sr]).max() > 0
    st = dsp.stutter(noise, sr, 1.0, 1.0, 0.25)
    a = int(1.0 * sr) + 500
    assert np.allclose(st[a:a + 100], st[a + int(0.25 * sr):a + int(0.25 * sr) + 100])


def test_timeline_section_references():
    tl = Timeline(120, "beats", [{"name": "pre", "start": 16, "end": 32}, {"name": "drop-a", "start": 32, "end": 64}])
    assert tl.to_seconds(4) == 2.0
    assert tl.to_seconds("pre") == 8.0 and tl.to_seconds("pre.end") == 16.0
    assert tl.to_seconds("pre.end-1") == 15.5 and tl.to_seconds("drop-a+2") == 17.0
    with pytest.raises(ValueError):
        tl.to_seconds("nope")
    secs = Timeline(120, "seconds", [{"name": "pre", "start": 16, "end": 32}])
    assert secs.to_seconds(3) == 3 and secs.to_seconds("pre+1") == 9.0


def _write_stems(tmp_path, sr=44100, seconds=16.0, bpm=120):
    """kick on every beat (low thump), a bass drone, a bright pad; 4 bars + 4 bars."""
    n = int(seconds * sr)
    t = np.arange(n) / sr
    spb = 60 / bpm
    kick = np.zeros(n)
    beats = np.arange(0, seconds, spb)
    for b in beats:
        i = int(b * sr)
        k = np.arange(min(4000, n - i))
        kick[i:i + len(k)] += 0.8 * np.sin(2 * np.pi * 55 * k / sr) * np.exp(-k / 1500)
    bass = 0.3 * np.sin(2 * np.pi * 55 * t)
    pad = 0.15 * np.sign(np.sin(2 * np.pi * 440 * t)) * 0.5
    stems = tmp_path / "stems"
    stems.mkdir()
    for name, x in (("drums", kick), ("bass", bass), ("synths", pad)):
        write_wav(stems / f"{name}.wav", np.stack([x, x], 1), sr, 24)
    score = {"bpm": bpm, "sections": [{"name": "a", "start": 0, "end": 16}, {"name": "b", "start": 16, "end": 32}],
             "parts": {"kick": [{"start": float(b / spb), "duration": 0.25, "pitch": 36, "velocity": 110}
                                for b in beats]}}
    (tmp_path / "score.json").write_text(json.dumps(score))
    return beats


def test_mix_spec_end_to_end(tmp_path):
    _write_stems(tmp_path)
    spec = {
        "score": "score.json",
        "stems": {"drums": {}, "bass": {"mono_below_hz": 120},
                  "synths": {"automation": {"lowpass_hz": [["a", 400], ["b", 18000]]}, "width": 1.2}},
        "sidechain": [{"trigger": {"part": "kick"}, "targets": ["bass", "synths"], "depth_db": 6,
                       "section_depth_db": {"b": 10}}],
        "cuts": [{"start": "a.end-1", "end": "a.end", "keep": ["drums"]}],
        "edits": [{"type": "tape_stop", "start": "b+12", "length": 2, "silence_after": True},
                  {"type": "telephone", "start": 0, "end": 4, "stems": ["synths"]}],
        "bus": {"eq": [{"type": "highpass", "freq": 25, "order": 4}], "glue": {"threshold_db": -18, "ratio": 2}},
        "master": {"target_lufs": -12, "ceiling_dbtp": -1.0, "fade_out": 1},
        "export": {"basename": "out", "mp3": False, "spectrogram": False},
    }
    (tmp_path / "mix.json").write_text(json.dumps(spec))
    report = run_mix(tmp_path / "mix.json")
    m = report["master"]
    assert m["integrated_lufs"] == pytest.approx(-12, abs=0.3)
    assert m["true_peak_dbtp"] <= -1.0
    y, sr = read_wav(report["files"]["wav"])
    assert sr == 44100 and y.shape[1] == 2
    # the cut silences everything but drums in the last beat of section a
    seg = y[int(7.6 * sr):int(7.95 * sr)]
    assert np.abs(seg).max() > 0.01
    assert any("cuts[0]" in a for a in report["applied"]) and any("sidechain[0]" in a for a in report["applied"])
    # tape stop runs 14-15 s (beat 28 + 2 beats at 120 BPM), then silence
    assert np.abs(y[int(14.0 * sr):int(14.3 * sr)]).max() > 0.01
    assert np.abs(y[int(15.02 * sr):]).max() < 1e-3
    assert set(report["sections"]["mix"]) == {"a", "b"}
    assert json.loads((tmp_path / "out_report.json").read_text())["master"]["integrated_lufs"] == m["integrated_lufs"]


def test_mix_spec_errors(tmp_path):
    _write_stems(tmp_path)
    with pytest.raises(MixError):
        run_mix({"score": str(tmp_path / "score.json"), "stems_dir": str(tmp_path / "stems"),
                 "stems": {"drums": {}, "nope": {}}}, tmp_path)
    with pytest.raises(MixError):
        run_mix({"score": str(tmp_path / "score.json"), "stems_dir": str(tmp_path / "stems"),
                 "sidechain": [{"trigger": {"part": "snare"}, "targets": ["bass"]}]}, tmp_path)
