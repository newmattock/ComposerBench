"""Song specs: resolution, preflight, and a full build against the serve emulator.

The emulator (EMU_RENDER_AUDIO=1) renders a short sine burst per note on every
audible track, so the stem logic can be checked numerically: the stems must sum
to the mix.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from composerbench.analysis.wav import read_wav
from composerbench.song import build as build_mod
from composerbench.song.build import SongSpecInvalid, build_song, check_song
from composerbench.song.examples import example_song
from composerbench.song.spec import SongSpec, resolve

EMULATOR = Path(__file__).with_name("_serve_emulator.py")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGDA_SOCK", raising=False)
    monkeypatch.setenv("EMU_RENDER_AUDIO", "1")
    monkeypatch.setenv("EMU_CATALOG", "1")
    cli = tmp_path / "magda_cli_emu"
    cli.write_text(f'#!/bin/sh\nEMU_PROTOCOL=2 exec "{sys.executable}" "{EMULATOR}" "$@"\n')
    cli.chmod(0o755)
    return cli


def test_resolve_merges_presets_and_overrides():
    spec = SongSpec.model_validate({
        "title": "t", "bpm": 120,
        "tracks": [{"name": "bass", "preset": "bass_808", "params": {"Glide": 80}, "volume": 0.3,
                    "fx_params": {0: {"Drive": 4}}, "notes": [{"start": 0, "duration": 1, "pitch": 40}],
                    "transpose": 12},
                   {"name": "pad", "instrument": {"device": "magda_polysynth", "params": {"Cutoff": 900}},
                    "fx": [{"device": "magda_reverb", "params": {"Engine": "Hall"}}], "stem": "keys"}]})
    bass, pad = resolve(spec)
    assert bass.instrument.device == "magda_polysynth" and bass.instrument.params["Glide"] == 80
    assert bass.instrument.params["Voice Mode"] == {"label": "Legato", "value": 2}
    assert bass.fx[0].params["Drive"] == 4 and bass.fx[0].params["Mode"] == {"label": "Tube", "value": 4}
    assert bass.volume == 0.3 and bass.stem == "bass" and bass.notes[0].pitch == 52
    assert pad.stem == "keys" and pad.volume == 0.5 and pad.notes == []
    with pytest.raises(ValueError):
        SongSpec.model_validate({"title": "t", "tracks": [{"name": "a", "preset": "pluck"},
                                                          {"name": "a", "preset": "pluck"}]})
    with pytest.raises(ValueError):
        resolve(SongSpec.model_validate({"title": "t", "bpm": 1, "tracks": [{"name": "x"}]}))


def test_preflight_catches_bad_specs(tmp_path):
    spec, score = example_song()
    spec["score"] = score
    good = check_song(spec, tmp_path)
    assert good["ok"], good["errors"]
    assert good["stems"] == ["drums", "bass", "synths", "lead", "fx"]
    assert any(i.startswith("harmony: 0 clash") for i in good["info"])

    bad = json.loads(json.dumps(spec))
    bad["tracks"][0]["params"] = {"body_pich": 50, "Gain": 99}
    bad["tracks"][1]["fx"] = [{"device": "magda_kick"}]
    bad["tracks"].append({"name": "x", "instrument": {"device": "magda_reverb"}})
    bad["render"]["stems"] = ["drums", "vocals"]
    rep = check_song(bad, tmp_path)
    assert not rep["ok"]
    text = "\n".join(rep["errors"])
    assert "body_pich" in text and "body_pitch" in text
    assert "is an instrument, not an effect" in text and "is an effect, not an instrument" in text
    assert "vocals" in text
    assert any("Gain" in w and "clamped" in w for w in rep["warnings"])

    late = json.loads(json.dumps(spec))
    late["length_beats"] = 16
    assert not check_song(late, tmp_path)["ok"]


def test_preflight_refuses_a_full_disk(tmp_path, monkeypatch):
    spec, score = example_song()
    spec["score"] = score
    monkeypatch.setattr(build_mod, "free_bytes", lambda p: 50 * 2**20)
    rep = check_song(spec, tmp_path)
    assert not rep["ok"] and "disk" in rep["errors"][0]


async def test_build_example_song_on_emulator(tmp_path, engine):
    spec, score = example_song()
    spec["score"] = score
    out = tmp_path / "song"
    events = []
    report = await build_song(spec, out_dir=out, cli_path=engine, progress=lambda s, i: events.append(s))
    assert "preflight" in events and "done" in events and events.count("render") == 6
    # every parameter landed and read back; every note arrived
    assert report["readback_mismatch"] == []
    param_errors = [p for p in report["problems"] if "silent" not in p]
    assert param_errors == []
    for name, t in report["tracks"].items():
        assert t["notes_live"] == t["notes_written"], name
    labels = [r for rs in report["param_results"].values() for r in rs if r.get("how") == "label"]
    assert labels and all(r["ok"] for r in labels)
    # renders: mix + 5 stems, right length, and the stems sum to the mix
    assert set(report["renders"]) == {"mix", "stem:drums", "stem:bass", "stem:synths", "stem:lead", "stem:fx"}
    mix, sr = read_wav(out / "mix.wav")
    assert len(mix) / sr == pytest.approx(report["clip_beats"] * 60 / report["bpm"], abs=0.01)
    total = np.zeros_like(mix)
    for stem in ("drums", "bass", "synths", "lead", "fx"):
        x, _ = read_wav(out / "stems" / f"{stem}.wav")
        assert np.abs(x).max() > 0.001, stem
        total += x
    assert np.abs(total - mix).max() < 5e-4
    # outputs for the mix stage and for humans
    saved = json.loads((out / "session.json").read_text())
    assert saved["title"] == "composerbench example" and saved["timings"]["total"] >= 0
    played = json.loads((out / "score.json").read_text())
    assert played["bpm"] == 150 and len(played["parts"]["kick"]) == 24 and played["sections"][1]["name"] == "drop"
    assert (out / "spec.resolved.json").exists() and (out / build_mod.MARKER).exists()

    # a rebuild replaces its own project; someone else's project is left alone
    await build_song(spec, out_dir=out, cli_path=engine, stems=False)
    other = tmp_path / "other"
    (other / "project").mkdir(parents=True)
    with pytest.raises(SongSpecInvalid):
        await build_song(spec, out_dir=other, cli_path=engine)


async def test_build_refuses_invalid_spec_unless_forced(tmp_path, engine):
    spec, score = example_song()
    spec["score"] = score
    spec["tracks"][0]["params"] = {"no_such_param": 1}
    with pytest.raises(SongSpecInvalid):
        await build_song(spec, out_dir=tmp_path / "a", cli_path=engine)
    report = await build_song(spec, out_dir=tmp_path / "b", cli_path=engine, force=True, stems=["drums"])
    assert any("no_such_param" in p for p in report["problems"])
    assert set(report["renders"]) == {"mix", "stem:drums"}
