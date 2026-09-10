"""Semantic role registry tests."""
import json
from pathlib import Path

import pytest
import yaml

from composerbench.semantics.registry import SemanticRegistry, RoleRecipe


@pytest.fixture
def reg_dir(tmp_path):
    sem = tmp_path / "semantics"
    dev = tmp_path / "devices"
    sem.mkdir()
    dev.mkdir()
    (dev / "aliases.json").write_text(json.dumps({
        "surge_xt": "VST3-Surge XT-abc",
        "dragonfly_hall": "VST3-Dragonfly Hall Reverb-def",
        "chowmatrix": "VST3-ChowMatrix-123",
    }))
    (sem / "core.yaml").write_text(yaml.safe_dump({
        "roles": [
            {"slug": "warm_pad", "engine": "surge_xt",
             "params": [{"names": ["Cutoff", "cutoff"], "value": 0.38}],
             "space": {"reverb": "dragonfly_hall", "reverb_wet": 0.4},
             "register": [48, 72], "level": 0.58,
             "character": ["warm", "sustained"]},
            {"slug": "bell_answer", "engine": "magda_bell",
             "register": [72, 96], "character": ["bell", "distant"]},
        ]}))
    return tmp_path


def test_load_and_get(reg_dir):
    reg = SemanticRegistry.load_from(reg_dir)
    role = reg.get("warm_pad")
    assert role.engine == "surge_xt"
    assert role.space.reverb == "dragonfly_hall"
    assert role.params[0].names == ["Cutoff", "cutoff"]
    assert role.register_range == (48, 72)


def test_alias_resolution(reg_dir):
    reg = SemanticRegistry.load_from(reg_dir)
    assert reg.resolve_engine("surge_xt") == "VST3-Surge XT-abc"
    # internal device ids pass through untouched
    assert reg.resolve_engine("magda_bell") == "magda_bell"


def test_unknown_alias_rejected(reg_dir):
    bad = reg_dir / "semantics" / "bad.yaml"
    bad.write_text(yaml.safe_dump(
        {"roles": [{"slug": "x", "engine": "nope_plugin"}]}))
    with pytest.raises(ValueError, match="no alias"):
        SemanticRegistry.load_from(reg_dir)


def test_by_character(reg_dir):
    reg = SemanticRegistry.load_from(reg_dir)
    hits = reg.by_character("warm")
    assert [r.slug for r in hits] == ["warm_pad"]
    assert reg.by_character("nonexistent") == []


def test_merge_learned_respects_pinned(reg_dir):
    reg = SemanticRegistry.load_from(reg_dir)
    learned = reg_dir / "semantics" / "learned"
    learned.mkdir()
    (learned / "warm_pad.json").write_text(json.dumps({
        "role": "warm_pad",
        "params": [{"names": ["Cutoff"], "value": 0.62}]}))
    touched = reg.merge_learned(learned)
    assert touched == ["warm_pad"]
    assert reg.get("warm_pad").params[0].value == 0.62
