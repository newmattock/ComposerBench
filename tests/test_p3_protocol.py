"""Serve protocol 3 on the adapter: mixer, sends, sidechain, automation, clip
edits, master inserts, catalog, stem renders - against the serve emulator.

The same ops are exercised on the real engine by examples/p3_engine_smoke.py
and, when a binary is found, by test_p3_live below.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.analysis.wav import read_wav
from composerbench.engine import discover_cli
from composerbench.errors import UnsupportedOperation
from composerbench.model import Note
from composerbench.song.build import build_song
from composerbench.song.examples import example_song

EMULATOR = Path(__file__).with_name("_serve_emulator.py")


def _emulator(tmp_path, protocol):
    cli = tmp_path / f"magda_cli_p{protocol}"
    cli.write_text(f'#!/bin/sh\nEMU_PROTOCOL={protocol} exec "{sys.executable}" "{EMULATOR}" "$@"\n')
    cli.chmod(0o755)
    return cli


@pytest.fixture
def p3(tmp_path, monkeypatch):
    monkeypatch.setenv("EMU_RENDER_AUDIO", "1")
    monkeypatch.setenv("EMU_CATALOG", "1")
    return _emulator(tmp_path, 3)


async def _session(cli, tmp_path):
    daw = MagdaServeAdapter(cli, tmp_path / "p" / "p.mgd")
    await daw.initialize()
    return daw


async def test_protocol3_ops_round_trip(p3, tmp_path):
    daw = await _session(p3, tmp_path)
    try:
        kick, pad, _verb = [await daw.create_track(n) for n in ("kick", "pad", "verb")]
        assert (await daw.set_mixer("kick", volume_db=-6))["volume"] == pytest.approx(0.501, abs=1e-3)
        assert (await daw.set_mixer(pad.id, pan=-0.5))["pan"] == -0.5
        sends = (await daw.set_send("pad", "verb", level_db=-6))["sends"]
        assert sends[0]["levelDb"] == pytest.approx(-6, abs=1e-3)
        assert (await daw.set_send("pad", "verb", remove=True))["sends"] == []
        ducker = await daw.add_effect("pad", "sidechain")
        assert (await daw.set_sidechain(ducker, "kick"))["sidechain"] == {"type": "midi", "source": 1}
        limiter = await daw.add_effect("master", "magda_limiter")
        assert limiter > ducker
        lane = await daw.write_automation({"kind": "volume", "track": "pad"},
                                          [{"beat": 0, "value": -40}, {"beat": 8, "value": 0}])
        assert lane["points"] == 2 and lane["target"] == {"kind": "volume", "trackId": 2}
        clip = await daw.clip_edit("create", track="kick", start=0, length=4, name="hits")
        moved = await daw.clip_edit("move", clip=clip["id"], start=8)
        assert moved["startBeat"] == 8.0
        assert (await daw.engine_catalog())["instruments"]
        await daw.load_device(kick.id, "magda_kick")
        c = await daw.create_clip(kick.id, "k", 0.0, 4.0)
        await daw.add_notes(kick.id, c.id, [Note(start=b, duration=0.25, pitch=36, velocity=110) for b in range(4)])
        from composerbench.adapter.base import RenderRequest
        with pytest.raises(ValueError):
            await daw.render(RenderRequest(output_path=tmp_path / "x.wav", track_ids=[]))
        stems = await daw.render_stems(tmp_path / "stems", track_ids=[kick.id], length_beats=4)
        assert [s["name"] for s in stems] == ["kick"] and Path(stems[0]["file"]).exists()
    finally:
        await daw.shutdown()


async def test_protocol2_engines_get_a_clear_error(tmp_path):
    daw = await _session(_emulator(tmp_path, 2), tmp_path)
    try:
        await daw.create_track("a")
        assert not await daw.supports_isolated_render()
        with pytest.raises(UnsupportedOperation) as err:
            await daw.set_mixer("a", volume_db=-3)
        assert "protocol 3" in str(err.value.to_dict())
    finally:
        await daw.shutdown()


async def test_song_build_renders_stems_from_their_own_tracks(p3, tmp_path):
    spec, score = example_song()
    spec["score"] = score
    out = tmp_path / "song"
    report = await build_song(spec, out_dir=out, cli_path=p3)
    assert report["stem_method"] == "isolated tracks"
    mix, _ = read_wav(out / "mix.wav")
    total = sum(read_wav(out / "stems" / f"{s}.wav")[0] for s in ("drums", "bass", "synths", "lead", "fx"))
    assert np.abs(total - mix).max() < 5e-4
    # faders were never zeroed: the saved project keeps every track's real volume
    saved = json.loads((out / "session.json").read_text())
    assert all(t["volume"] > 0 for t in saved["tracks"].values())


@pytest.mark.skipif(discover_cli() is None, reason="magda_cli not built")
async def test_p3_live(tmp_path):
    daw = await _session(discover_cli(), tmp_path)
    try:
        if not await daw.supports_isolated_render():
            pytest.skip("engine is older than protocol 3")
        await daw.create_track("kick")
        await daw.create_track("verb")
        assert (await daw.set_mixer("kick", volume_db=-6))["volumeDb"] == pytest.approx(-6, abs=1e-3)
        await daw.add_effect("verb", "magda_reverb")
        assert (await daw.set_send("kick", "verb", level_db=-12))["sends"][0]["levelDb"] == pytest.approx(-12, abs=1e-3)
        assert {"instruments", "effects"} <= set(await daw.engine_catalog())
    finally:
        await daw.shutdown()


async def test_mcp_tools_drive_protocol3(p3, tmp_path):
    from composerbench.mcp.server import TOOLS, MCPServer, ServerConfig
    names = {t["name"]: t for t in TOOLS}
    for n in ("music_mixer_set", "music_automation_write", "music_clip_edit", "music_stems_render"):
        assert names[n]["inputSchema"]["additionalProperties"] is False
    server = MCPServer(None, ServerConfig(adapter="magda_serve", cli_path=str(p3), workspace=str(tmp_path)))

    async def call(name, args):
        result = await server.call_tool(name, args)
        assert not result.get("isError"), result
        return result["structuredContent"]
    try:
        await _drive(call, tmp_path)
    finally:
        await server.close()


async def _drive(call, tmp_path):
    await call("music_project_new", {"name": "p3"})
    for track in ("kick", "pad", "verb"):
        await call("music_track_create", {"name": track})
    channel = (await call("music_mixer_set", {"track": "pad", "volume_db": -4, "pan": 0.3,
                                              "sends": [{"to": "verb", "level_db": -10}]}))["channel"]
    assert channel["pan"] == 0.3 and channel["sends"][0]["levelDb"] == pytest.approx(-10, abs=1e-3)
    ducker = await call("music_fx_add", {"track": "pad", "name": "sidechain", "sidechain_from": "kick"})
    assert ducker["sidechain"]["source"] == 1
    assert (await call("music_fx_add", {"track": "master", "name": "magda_limiter"}))["position"] == "pre"
    lane = await call("music_automation_write", {"target": {"kind": "volume", "track": "pad"},
                                                 "points": [{"beat": 0, "value": -30}, {"beat": 4, "value": 0}]})
    assert lane["points"] == 2
    clip = (await call("music_clip_edit", {"action": "create", "track": "kick", "start": 0, "length": 4}))["clip"]
    assert clip["lengthBeats"] == 4.0
    stems = (await call("music_stems_render", {"output_dir": str(tmp_path / "stems"), "tracks": ["kick"],
                                               "length_beats": 4}))["stems"]
    assert stems[0]["name"] == "kick"
    live = await call("music_device_catalog", {"kind": "effect", "live": True})
    assert live["engine"]["effects"]


async def test_mcp_protocol3_tools_refuse_the_fake_engine(tmp_path):
    from composerbench.mcp.server import MCPServer, ServerConfig
    server = MCPServer(None, ServerConfig(adapter="fake", workspace=str(tmp_path)))
    await server.call_tool("music_project_new", {"name": "f"})
    result = await server.call_tool("music_mixer_set", {"track": "x", "pan": 0.1})
    assert result["isError"] and "protocol 3" in str(result["structuredContent"])


async def test_balance_pass_moves_faders_to_their_levels(p3, tmp_path):
    from composerbench.mixing.balance import balance_song, track_level
    spec, score = example_song()
    spec["score"] = score
    out = tmp_path / "song"
    await build_song(spec, out_dir=out, cli_path=p3)
    first = await balance_song(out, cli_path=p3, apply=True, levels={"lead": -2.0})
    rows = {r["track"]: r for r in first["tracks"]}
    assert first["applied"] and rows["lead"]["level"] == -2.0
    moved = [r for r in rows.values() if r.get("target_lufs") is not None]
    assert moved and all(r["measured_lufs"] is not None for r in moved)
    # stems and mix were rebuilt from the track renders with the new faders
    mix, _ = read_wav(out / "mix.wav")
    total = sum(read_wav(p)[0] for p in (out / "stems").glob("*.wav"))
    assert np.abs(total - mix).max() < 1e-4
    saved = json.loads((out / "session.json").read_text())
    assert saved["tracks"]["lead"]["volume"] == rows["lead"]["new_volume"]
    # a second pass finds every part already where it belongs
    second = await balance_song(out, cli_path=p3, levels={"lead": -2.0})
    assert all(abs(r["gain_db"]) < 0.3 for r in second["tracks"] if r.get("target_lufs") is not None)
    assert track_level({"a": -20.0, "b": -21.0, "c": -40.0}) == -20.5


def test_preset_levels_are_calibrated():
    from composerbench.devices.levels import LEVELS, load_calibration, volume_for_level
    from composerbench.devices.presets import PRESETS, get_preset
    assert set(LEVELS) == set(PRESETS)
    calibrated = load_calibration()["presets"]
    assert len(calibrated) >= len(PRESETS) - 1          # Surge XT needs the plugin
    kick = get_preset("kick_punch")
    assert kick["level"] == 0.0 and kick["lufs_at_unity"] is not None
    volume, clamped = volume_for_level(-1.5, -23.9, -23.5)
    assert volume == pytest.approx(10 ** (-1.1 / 20), rel=1e-3) and not clamped
    assert volume_for_level(10, -40, -20)[1]


def test_level_sets_the_fader_from_calibration():
    from composerbench.song.spec import SongSpec, resolve
    spec = SongSpec.model_validate({"title": "t", "bpm": 120, "tracks": [
        {"name": "kick", "preset": "kick_punch"}, {"name": "pad", "preset": "pad_sweep", "level": -3},
        {"name": "fixed", "preset": "pluck", "volume": 0.3}, {"name": "x", "instrument": {"device": "magda_fm"}}]})
    kick, pad, fixed, x = resolve(spec)
    assert kick.volume_source == "preset level" and pad.volume_source == "level"
    assert fixed.volume == 0.3 and fixed.volume_source == "volume"
    assert x.volume == 0.5 and x.volume_source == "default"


async def test_plugin_names_prefer_vst3_over_lv2(tmp_path):
    fake_cli = tmp_path / "magda_cli"
    fake_cli.write_text("#!/bin/sh\n")
    fake_cli.chmod(0o755)
    daw = MagdaServeAdapter(fake_cli, tmp_path / "p.mgd")
    plugs = [{"id": "LV2-Surge XT-1", "name": "Surge XT", "format": "LV2"},
             {"id": "VST3-Surge XT-2", "name": "Surge XT", "format": "VST3"},
             {"id": "LV2-Only-3", "name": "Only", "format": "LV2"}]

    async def listing(format_filter=None):
        return plugs
    daw.list_plugins = listing
    assert await daw.resolve_plugin("Surge XT") == "VST3-Surge XT-2"
    assert await daw.resolve_plugin("LV2-Surge XT-1") == "LV2-Surge XT-1"     # an explicit id is kept
    assert await daw.resolve_plugin("Only") == "Only"
