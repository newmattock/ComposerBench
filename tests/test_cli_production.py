"""The production CLI verbs, run in-process (so coverage sees them) against the
serve emulator where an engine is needed."""
import json
import math
import shutil
import struct
import sys
import wave
from pathlib import Path

import pytest

from composerbench.cli.main import main

EMULATOR = Path(__file__).with_name("_serve_emulator.py")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("EMU_RENDER_AUDIO", "1")
    monkeypatch.setenv("EMU_CATALOG", "1")
    cli = tmp_path / "magda_cli_emu"
    cli.write_text(f'#!/bin/sh\nEMU_PROTOCOL=3 exec "{sys.executable}" "{EMULATOR}" "$@"\n')
    cli.chmod(0o755)
    return cli


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip().startswith(("{", "[")) else out)


def _tone_wav(path: Path, seconds=2.0, sr=44100):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = b"".join(struct.pack("<hh", v, v) for v in
                          (int(8000 * math.sin(2 * math.pi * 220 * i / sr)) for i in range(int(seconds * sr))))
        w.writeframes(frames)
    return path


def test_catalog_presets_and_examples(capsys, tmp_path):
    code, devices = run(capsys, "devices", "--kind", "effect")
    assert code == 0 and all(d["kind"] == "effect" for d in devices)
    code, text = run(capsys, "devices", "magda_delay", "--text")
    assert code == 0 and "Division" in text
    code, missing = run(capsys, "devices", "nope")
    assert code == 1 and "devices" in missing
    code, presets = run(capsys, "presets")
    assert any(p["name"] == "bass_808" and p["level"] == -1.5 for p in presets)
    code, preset = run(capsys, "presets", "kick_punch")
    assert preset["lufs_at_unity"] is not None
    code, mix_example = run(capsys, "mix", "example")
    assert "master" in mix_example
    score_path = tmp_path / "score.json"
    code, spec = run(capsys, "song", "example", "--score-out", str(score_path))
    assert code == 0 and spec["score"] == str(score_path) and score_path.exists()


def test_song_check_and_score_check(capsys, tmp_path):
    _, spec = run(capsys, "song", "example")
    spec_path = tmp_path / "song.json"
    spec_path.write_text(json.dumps(spec))
    code, report = run(capsys, "song", "check", str(spec_path), "--out", str(tmp_path / "out"))
    assert code == 0 and report["ok"]
    score_path = tmp_path / "score.json"
    score_path.write_text(json.dumps(spec["score"]))
    code, checks = run(capsys, "score", "check", str(score_path), "--ranges", '{"bass": [24, 52]}', "--mono", "bass")
    assert code == 0 and {"harmony", "ranges", "sustain"} <= set(checks)
    code, _ = run(capsys, "song", "check")
    assert code == 2


def test_report_on_a_wav(capsys, tmp_path):
    wav = _tone_wav(tmp_path / "tone.wav")
    score = tmp_path / "score.json"
    score.write_text(json.dumps({"bpm": 120, "sections": [{"name": "a", "start": 0, "end": 2},
                                                          {"name": "b", "start": 2, "end": 4}]}))
    stems = tmp_path / "stems"
    stems.mkdir()
    shutil.copy(wav, stems / "x.wav")
    code, report = run(capsys, "report", str(wav), "--score", str(score), "--stems", str(stems))
    assert code == 0 and report["summary"]["integrated_lufs"] < 0 and set(report["sections"]["mix"]) == {"a", "b"}


def test_build_balance_and_mix_on_the_emulator(capsys, tmp_path, engine):
    _, spec = run(capsys, "song", "example")
    spec_path = tmp_path / "song.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "song"
    code, built = run(capsys, "--cli", str(engine), "song", "build", str(spec_path), "--out", str(out),
                      "--engine", str(engine))
    assert code == 0 and "stem:drums" in built["renders"]
    code, balanced = run(capsys, "song", "balance", str(out), "--engine", str(engine), "--apply",
                         "--levels", '{"lead": -2}')
    assert code == 0 and balanced["applied"] and any(t["track"] == "lead" for t in balanced["tracks"])
    mix_spec = json.loads(json.dumps(__import__("composerbench.mixing.spec", fromlist=["x"]).example_spec()))
    mix_spec["export"] = {"mp3": False, "spectrogram": False}
    mix_spec["stems"] = {s: {} for s in ("drums", "bass", "synths", "lead", "fx")}
    (out / "mix.json").write_text(json.dumps(mix_spec))
    code, mixed = run(capsys, "mix", str(out / "mix.json"))
    assert code == 0 and mixed["master"]["integrated_lufs"] is not None and "section_lufs" in mixed


def test_core_cli_commands_on_the_fake_engine(capsys, tmp_path):
    base = ["--adapter", "fake", "--project", str(tmp_path / "proj"), "--json"]
    code, status = run(capsys, *base, "status")
    assert code == 0 and status["title"] == "Untitled"
    for cmd in (["intent"], ["plan"], ["inspect", "project"]):
        code, _out = run(capsys, *base, *cmd)
        assert code == 0, cmd
    code, _snap = run(capsys, *base, "snapshot", "first")
    assert code == 0
    code, _ = run(capsys, *base, "mcp", "config")
    assert code == 0
