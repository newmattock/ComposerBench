"""Items 2-4 against the real MAGDA engine (skipped when magda_cli is not built).

    .venv/bin/python -m pytest -q tests/test_p2_magda_live.py -s

Each test boots its own `magda_cli serve` on a scratch project (~15 s boot).
Engines without serve protocol 2 still run the tests through the fallbacks;
the protocol-2-only assertions are skipped with the reason printed.
"""
import time

import pytest

from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.mcp.server import discover_cli
from composerbench.model import Note

CLI = discover_cli()
pytestmark = pytest.mark.skipif(CLI is None, reason="magda_cli not built (set COMPOSERBENCH_CLI)")


def _notes(count, length):
    return [Note(start=(i * 0.25) % length, duration=0.2, pitch=48 + i % 24, velocity=60 + i % 60)
            for i in range(count)]


async def _session(tmp_path, name="live"):
    daw = MagdaServeAdapter(CLI, tmp_path / name / f"{name}.mgd")
    (tmp_path / name).mkdir(exist_ok=True)
    await daw.initialize()
    return daw, await daw.features()


async def test_whole_clip_in_one_call(tmp_path):
    daw, feats = await _session(tmp_path)
    try:
        track = await daw.create_track("lead")
        await daw.load_device(track.id, "magda_polysynth")
        clip = await daw.create_clip(track.id, "part", 0.0, 256.0)
        count = 5040 if feats.get("bulkNotes") else 600   # the legacy path is O(n^2) in MAGDA
        t0 = time.perf_counter()
        assert await daw.set_notes(track.id, clip.id, _notes(count, 256.0)) == count
        elapsed = time.perf_counter() - t0
        print(f"\n{count} notes in {elapsed:.2f} s (protocol {feats['protocol']})")
        assert len(await daw.get_notes(track.id)) == count
        # replacing keeps the clip itself
        assert await daw.set_notes(track.id, clip.id, _notes(16, 256.0)) == 16
        clips = await daw.get_clips(track.id)
        assert len(clips) == 1 and len(clips[0].notes) == 16
        if feats.get("bulkNotes"):
            assert elapsed < 20, "bulk note op should not scale quadratically"
    finally:
        await daw.shutdown()


async def test_internal_choice_labels_round_trip(tmp_path):
    daw, _ = await _session(tmp_path)
    try:
        track = await daw.create_track("fx")
        await daw.load_device(track.id, "magda_polysynth")
        reverb = await daw.add_fx(track.id, "magda_reverb")
        params = await daw.get_parameters(track.id, reverb)
        engine = next(p for p in params.values() if p["name"] == "Engine")
        assert len(engine["choices"]) >= 2, engine
        second = engine["choices"][1]
        await daw.set_parameter(track.id, reverb, "Engine", second)
        info = await daw.describe_parameter(track.id, reverb, "Engine")
        assert info["text"] == second and info["value"] == 1.0
    finally:
        await daw.shutdown()


async def test_hosted_plugin_label_and_persistence(tmp_path):
    daw, feats = await _session(tmp_path)
    try:
        surge = next((p for p in await daw.list_plugins() if p.get("name") == "Surge XT"), None)
        if surge is None:
            pytest.skip("Surge XT is not installed")
        track = await daw.create_track("saws")
        dev = await daw.add_plugin(track.id, surge["id"], as_instrument=True)
        params = await daw.get_parameters(track.id, dev)
        assert any(p.get("owner") == "host" for p in params.values()), "wrapper pair not flagged"
        if feats.get("paramInfo"):
            await daw.set_parameter(track.id, dev, "A Filter 1 Type", "LP 12 dB")
        else:
            await daw.set_parameter(track.id, dev, "A Filter 1 Type", 0.03)
        await daw.set_parameter(track.id, dev, "A Filter 1 Cutoff", 0.58)
        await daw.save_project()
        if not feats.get("pluginPersistence"):
            pytest.skip("this MAGDA build hosts plugins outside the project (protocol 1)")
    finally:
        await daw.shutdown()
    again = MagdaServeAdapter(CLI, daw.project)      # reopen: no initialize(), it would re-init
    try:
        await again.features()
        devices = await again.get_devices_full(track.id)
        saws = [d for d in devices if d.get("name") == "Surge XT"]
        assert saws, [d.get("name") for d in devices]
        params = await again.get_parameters(track.id, saws[0]["id"])
        ftype = next(p for p in params.values() if p["name"] == "A Filter 1 Type")
        cutoff = next(p for p in params.values() if p["name"] == "A Filter 1 Cutoff")
        assert ftype.get("text") == "LP 12 dB", ftype
        assert cutoff["value"] == pytest.approx(0.58, abs=1e-3)
    finally:
        await again.shutdown()


async def test_warm_pad_role_switches_surges_filter_on(tmp_path):
    from composerbench.semantics.registry import load_default
    try:
        reg = load_default()
        recipe = reg.get("warm_pad")
    except (FileNotFoundError, KeyError):
        pytest.skip("no warm_pad role in ~/.musicbench/semantics")
    if not any(p.label for p in recipe.params):
        pytest.skip("warm_pad recipe predates the filter-type fix")
    daw, _ = await _session(tmp_path)
    try:
        report = await daw.apply_role("pad", recipe, reg)
        assert not any("?" in s for s in report["steps"]), report["steps"]
        first = report["params"][0]
        assert first["param"] == "A Filter 1 Type" and first["via"] in ("label", "value-fallback")
        params = await daw.get_parameters("track:pad", report["device"])
        ftype = next(p for p in params.values() if p["name"] == "A Filter 1 Type")
        assert ftype["value"] > 0.02, "filter left Off"
    finally:
        await daw.shutdown()
