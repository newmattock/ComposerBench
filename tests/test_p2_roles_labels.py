"""Role recipes that speak in labels ("LP 12 dB") and learned values that know
the context they were learned in (item 4 of the agentic-music review)."""
import json
import sys
from pathlib import Path

import pytest

from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.semantics.registry import ParamSpec, RoleRecipe, SemanticRegistry

EMULATOR = Path(__file__).with_name("_serve_emulator.py")
ALIASES = {"surge_xt": "VST3-Surge XT-1", "dragonfly_hall": "VST3-Dragonfly Hall Reverb-2"}


def pad(label="LP 12 dB", value=0.03):
    return RoleRecipe(slug="warm_pad", engine="surge_xt", level=0.58,
                      params=[ParamSpec(names=["A Filter 1 Type", "Filter 1 Type"], label=label, value=value),
                              ParamSpec(names=["A Filter 1 Cutoff", "Cutoff", "cutoff"], value=0.58)],
                      space={"reverb": "dragonfly_hall", "reverb_wet": 0.35})


@pytest.fixture
def emu(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGDA_SOCK", raising=False)
    made = []

    def make(protocol=2):
        cli = tmp_path / f"magda_cli_p{protocol}"
        cli.write_text(f'#!/bin/sh\nEMU_PROTOCOL={protocol} exec "{sys.executable}" "{EMULATOR}" "$@"\n')
        cli.chmod(0o755)
        project = tmp_path / f"p{protocol}" / "song.mgd"
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


def by_name(params, name, owner=None):
    return next(p for p in params.values() if p["name"] == name and p.get("owner") == owner)


async def test_role_label_is_resolved_against_the_plugins_own_labels(emu):
    daw = emu(2)
    await daw.initialize()
    report = await daw.apply_role("pad", pad(), SemanticRegistry({}, ALIASES))
    ftype, cutoff = report["params"]
    assert (ftype["param"], ftype["via"], ftype["text"]) == ("A Filter 1 Type", "label", "LP 12 dB")
    assert ftype["value"] == pytest.approx(0.005 + 0.99 / 5)      # Surge's own position
    assert cutoff["param"] == "A Filter 1 Cutoff" and cutoff["value"] == 0.58
    assert not any("?" in s for s in report["steps"])
    # reverb_wet keeps driving the wrapper gain, never Dragonfly's own Dry Level
    reverb = [d for d in await daw.get_devices_full("track:pad") if d["name"] == "Dragonfly Hall Reverb"][0]
    params = await daw.get_parameters("track:pad", reverb["id"])
    assert by_name(params, "Wet Level", "host")["value"] == 0.35
    assert by_name(params, "Dry Level")["value"] == 0.8
    await daw.shutdown()


async def test_legacy_engine_falls_back_to_the_authored_number(emu):
    daw = emu(1)
    await daw.initialize()
    report = await daw.apply_role("pad", pad(), SemanticRegistry({}, ALIASES))
    ftype = report["params"][0]
    assert ftype["via"] == "value-fallback" and ftype["value"] == 0.03
    assert "unconfirmed" in report["steps"][2] and "?" not in report["steps"][2]
    devices = await daw.get_devices_full("track:pad")
    assert {d["name"] for d in devices} == {"Surge XT", "Dragonfly Hall Reverb"}
    assert all(d["persistent"] is False for d in devices)
    await daw.shutdown()


async def test_unknown_label_fails_loudly_with_the_real_choices(emu):
    daw = emu(2)
    await daw.initialize()
    report = await daw.apply_role("pad", pad(label="LP 99 dB"), SemanticRegistry({}, ALIASES))
    ftype = report["params"][0]
    assert ftype["error"] and "LP 12 dB" in ftype["choices"]
    assert any("?" in s for s in report["steps"])          # actions.apply turns this into an error
    await daw.shutdown()


async def test_duplicate_names_prefer_the_plugins_own_control(emu):
    daw = emu(2)
    await daw.initialize()
    track = await daw.create_track("verb")
    dev = await daw.add_plugin(track.id, "Dragonfly Hall Reverb")
    await daw.set_parameter(track.id, dev, "Dry Level", 0.5)
    await daw.set_parameter(track.id, dev, "host:Wet Level", "0.9")
    params = await daw.get_parameters(track.id, dev)
    assert by_name(params, "Dry Level")["value"] == 0.5
    assert by_name(params, "Dry Level", "host")["value"] == 0.0
    assert by_name(params, "Wet Level", "host")["value"] == 0.9
    await daw.shutdown()


async def test_legacy_engine_refuses_ambiguous_names_instead_of_hitting_the_wrapper(emu):
    from composerbench.errors import UnsupportedOperation
    daw = emu(1)
    await daw.initialize()
    track = await daw.create_track("verb")
    dev = await daw.add_plugin(track.id, "Dragonfly Hall Reverb")
    with pytest.raises(UnsupportedOperation):
        await daw.set_parameter(track.id, dev, "Dry Level", 0.5)
    await daw.set_parameter(track.id, dev, "host:Wet Level", 0.4)    # first of its name: fine
    assert by_name(await daw.get_parameters(track.id, dev), "Wet Level", "host")["value"] == 0.4
    await daw.shutdown()


def test_param_spec_needs_a_value_or_a_label():
    with pytest.raises(ValueError):
        ParamSpec(names=["Cutoff"])
    assert ParamSpec(names=["Filter 1 Type"], label="LP 12 dB").value is None


def _write(tmp_path, name, doc):
    learned = tmp_path / "learned"
    learned.mkdir(exist_ok=True)
    (learned / name).write_text(json.dumps(doc))
    return learned


def test_learned_values_are_refused_outside_their_context(tmp_path):
    reg = SemanticRegistry({"warm_pad": pad()}, ALIASES)
    # 1) the retired Surge cutoff: explicitly invalid
    learned = _write(tmp_path, "warm_pad.json", {"role": "warm_pad", "invalid": True,
                                                 "params": [{"names": ["cutoff"], "value": 0.6}]})
    assert reg.merge_learned(learned) == [] and reg.get("warm_pad").params[1].value == 0.58
    # 2) legacy doc without context, but the recipe sets other things too
    _write(tmp_path, "warm_pad.json", {"role": "warm_pad", "params": [{"names": ["cutoff"], "value": 0.6}]})
    assert reg.merge_learned(learned) == []
    assert "other parameters" in reg.skipped_learned[0]["reason"]
    # 3) learned with the filter switched on: accepted
    context = [{"names": ["A Filter 1 Type", "Filter 1 Type"], "label": "LP 12 dB", "value": 0.03}]
    _write(tmp_path, "warm_pad.json", {"role": "warm_pad", "context": context,
                                       "params": [{"names": ["cutoff"], "value": 0.52}]})
    assert reg.merge_learned(learned) == ["warm_pad"] and reg.get("warm_pad").params[1].value == 0.52
    # 4) ...but not once the recipe picks another filter
    other = SemanticRegistry({"warm_pad": pad(label="LP 24 dB")}, ALIASES)
    assert other.merge_learned(learned) == []
    assert "changed" in other.skipped_learned[0]["reason"]


def test_sweep_records_its_context(tmp_path):
    from composerbench.experiments.sweep import context_specs, save_learned
    recipe = pad()
    assert [p.names[0] for p in context_specs(recipe, ["cutoff"])] == ["A Filter 1 Type"]
    out = save_learned("warm_pad", ["cutoff"], {"value": 0.55, "metric": "high_energy"},
                       tmp_path, recipe=recipe)
    doc = json.loads(out.read_text())
    assert doc["context"] == [{"names": ["A Filter 1 Type", "Filter 1 Type"],
                               "label": "LP 12 dB", "value": 0.03}]
    reg = SemanticRegistry({"warm_pad": pad()}, ALIASES)
    assert reg.merge_learned(tmp_path) == ["warm_pad"]
