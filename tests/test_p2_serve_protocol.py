"""MagdaServeAdapter against an emulated `magda_cli serve` (protocols 1 and 2).

These pin down *how* ComposerBench talks to the engine: whole clips in one
request, one save per batch instead of one per command, cached dumps, labels
from the live dump or (legacy) from the saved project, and hosted plugins that
survive a reload.
"""
import json
import sys
from pathlib import Path

import pytest

from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.client import ComposerBench
from composerbench.composition.state import SidecarStore
from composerbench.model import Note

EMULATOR = Path(__file__).with_name("_serve_emulator.py")


@pytest.fixture
def emu(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGDA_SOCK", raising=False)
    made = []

    def make(protocol=2, name="song"):
        cli = tmp_path / f"magda_cli_p{protocol}"
        cli.write_text(f'#!/bin/sh\nEMU_PROTOCOL={protocol} exec "{sys.executable}" "{EMULATOR}" "$@"\n')
        cli.chmod(0o755)
        project = tmp_path / f"p{protocol}" / f"{name}.mgd"
        project.parent.mkdir(parents=True, exist_ok=True)
        daw = MagdaServeAdapter(cli, project)
        made.append(daw)
        return daw

    yield make
    import asyncio
    for daw in made:
        try:
            asyncio.get_event_loop().run_until_complete(daw.shutdown())
        except Exception:
            pass


def stats(daw):
    return json.loads(daw.project.with_name(daw.project.name + ".emu.json").read_text())


async def _track_with_clip(daw, length=64.0):
    await daw.initialize()
    track = await daw.create_track("lead")
    await daw.load_device(track.id, "magda_polysynth")
    clip = await daw.create_clip(track.id, "part", 0.0, length)
    return track, clip


def _notes(n, length=64.0):
    return [Note(start=(i * 0.25) % length, duration=0.2, pitch=48 + i % 24, velocity=90) for i in range(n)]


async def test_protocol2_writes_a_whole_clip_in_one_request(emu):
    daw = emu(2)
    track, clip = await _track_with_clip(daw)
    before = stats(daw)["requests"].get("notes", 0)
    assert await daw.set_notes(track.id, clip.id, _notes(1000)) == 1000
    s = stats(daw)
    assert s["requests"]["notes"] == before + 1
    assert s["max_notes_per_request"] == 1000
    assert len(await daw.get_notes(track.id)) == 1000
    # replace keeps the clip (same id and ComposerBench name)
    assert await daw.set_notes(track.id, clip.id, _notes(10)) == 10
    clips = await daw.get_clips(track.id)
    assert [c.id for c in clips] == [clip.id] and clips[0].name == "part"
    assert len(clips[0].notes) == 10
    caps = daw.capabilities()
    assert caps.bulk_notes and caps.explicit_save and caps.plugin_persistence
    await daw.shutdown()


async def test_protocol2_batch_saves_once(emu):
    daw = emu(2)
    mb = ComposerBench(daw, SidecarStore(daw.project.parent))
    await mb.initialize()
    saves0 = stats(daw)["saves"]
    await mb.batch([{"type": "create_track", "name": "a"}, {"type": "create_track", "name": "b"},
                    {"type": "load_device", "track": "a", "name": "magda_polysynth"},
                    {"type": "create_clip", "track": "a", "name": "c", "start_beats": 0, "length_beats": 16}])
    # one save for the transaction snapshot + one at commit, none per command
    assert stats(daw)["saves"] - saves0 == 2
    await mb.close()


async def test_dump_is_cached_until_the_next_edit(emu):
    daw = emu(2)
    await daw.initialize()
    await daw.create_track("x")               # the edit, then one dump to find the new track
    d0 = stats(daw)["requests"].get("dump", 0)
    for _ in range(3):
        await daw.get_tracks()
        await daw.get_clips()
    assert stats(daw)["requests"]["dump"] == d0        # served from cache
    await daw.create_track("y")
    assert [t.name for t in await daw.get_tracks()] == ["x", "y"]
    assert stats(daw)["requests"]["dump"] == d0 + 1
    await daw.shutdown()


async def test_protocol2_labels_in_and_out(emu):
    daw = emu(2)
    track, _ = await _track_with_clip(daw)
    fx = await daw.add_fx(track.id, "magda_reverb")
    params = await daw.get_parameters(track.id, fx)
    engine = next(p for p in params.values() if p["name"] == "Engine")
    assert engine["choices"] == ["Plate", "Hall", "Room"] and engine["text"] == "Plate"
    await daw.set_parameter(track.id, fx, "Engine", "Hall")
    engine = next(p for p in (await daw.get_parameters(track.id, fx)).values() if p["name"] == "Engine")
    assert engine["value"] == 1.0 and engine["text"] == "Hall"
    info = await daw.describe_parameter(track.id, fx, "Engine")
    assert info["valueTable"] == ["Plate", "Hall", "Room"]
    await daw.shutdown()


async def test_protocol1_labels_come_from_the_saved_project(emu):
    daw = emu(1)
    track, _ = await _track_with_clip(daw)
    fx = await daw.add_fx(track.id, "magda_delay")
    raw = (await daw.get_devices_full(track.id))[-1]["parameters"][0]
    assert raw["choices"] is None                     # the legacy dump says null...
    params = await daw.get_parameters(track.id, fx)
    division = next(p for p in params.values() if p["name"] == "Division")
    assert division["text"] == "1/4" and "1/8." in division["choices"]   # ...the .mgd does
    await daw.set_parameter(track.id, fx, "Division", "1/8.")
    division = next(p for p in (await daw.get_parameters(track.id, fx)).values() if p["name"] == "Division")
    assert division["value"] == 7.0
    assert not daw.capabilities().explicit_save
    await daw.shutdown()


async def test_protocol1_notes_fall_back_to_one_exec(emu):
    daw = emu(1)
    track, clip = await _track_with_clip(daw)
    await daw.add_notes(track.id, clip.id, _notes(300))
    s = stats(daw)
    assert "notes" not in s["requests"] and s["max_notes_per_request"] == 300
    assert await daw.set_notes(track.id, clip.id, _notes(5)) == 5   # rebuilt clip, same name
    clips = await daw.get_clips(track.id)
    assert len(clips) == 1 and clips[0].name == "part" and len(clips[0].notes) == 5
    # the caller's old clip id still reaches the rebuilt clip
    assert await daw.set_notes(track.id, clip.id, _notes(3)) == 3
    assert len((await daw.get_clips(track.id))[0].notes) == 3
    await daw.shutdown()


async def test_protocol2_hosted_plugin_survives_reload(emu, tmp_path):
    daw = emu(2)
    track, _ = await _track_with_clip(daw)
    plugins = await daw.list_plugins()
    dev = await daw.add_plugin(track.id, plugins[0]["id"], as_instrument=True)
    await daw.set_parameter(track.id, dev, "A Filter 1 Type", "LP 12 dB")
    await daw.save_project()
    await daw.shutdown()
    again = MagdaServeAdapter(daw.cli, daw.project)
    params = await again.get_parameters(track.id, dev)
    ftype = next(p for p in params.values() if p["name"] == "A Filter 1 Type")
    assert ftype["text"] == "LP 12 dB"
    await again.shutdown()


async def test_protocol1_plugin_lives_outside_the_model(emu):
    daw = emu(1)
    track, _ = await _track_with_clip(daw)
    ext = await daw.add_plugin(track.id, "VST3-Surge XT-1", as_instrument=True)
    assert ext >= 1000 and not daw.capabilities().plugin_persistence
    # still listed as a device this session, flagged as not saved
    listed = [d for d in await daw.get_devices_full(track.id) if d["id"] == ext]
    assert listed and listed[0]["persistent"] is False and listed[0]["name"] == "Surge XT"
    params = await daw.get_parameters(track.id, ext)
    assert params[0]["owner"] == "host" and params[0]["stableId"] == "host:Dry Level"
    await daw.set_parameter(track.id, ext, "A Filter 1 Cutoff", 0.7)
    cutoff = next(p for p in (await daw.get_parameters(track.id, ext)).values()
                  if p["name"] == "A Filter 1 Cutoff")
    assert cutoff["value"] == 0.7
    await daw.shutdown()
