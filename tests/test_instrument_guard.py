"""Instrument registry, load-guard, and audibility-gate regression tests.

Background: the MAGDA CLI accepts ANY instrument id for
add-internal-instrument (exit 0), but ids that match nothing render -120 dBFS
silence. These tests pin the three layers that make that failure impossible
to ship silently again:

1. check_audible - the rendered-audio silence constraint
2. MagdaCliAdapter._guard_instrument - fail-fast on unknown ids at load time
3. RevisionLoop min_peak_dbfs - silent renders auto-REJECT the revision
"""
import struct
import wave
from pathlib import Path

import pytest

from composerbench.adapter.fake import FakeDawAdapter
from composerbench.adapter.instruments import (
    KNOWN_AUDIBLE_INSTRUMENTS,
    write_manifest,
)
from composerbench.adapter.magda import MagdaCliAdapter
from composerbench.agent.loop import Action, LoopBudget, RevisionLoop
from composerbench.composition.state import CompositionPlan, Section
from composerbench.errors import UnsupportedOperation
from composerbench.evaluation.constraints import check_audible
from composerbench.model import Note

PLAN = CompositionPlan(
    sections=[Section(name="intro", bars=4, start_bar=1, target_energy=0.2),
              Section(name="climax", bars=4, start_bar=5, target_energy=0.9)]
)


def _adapter(tmp_path: Path) -> MagdaCliAdapter:
    """Adapter whose guard we can exercise without launching MAGDA.

    The guard runs before any subprocess work, so a dummy executable file
    satisfies the constructor's existence check.
    """
    dummy_cli = tmp_path / "dummy_magda_cli"
    dummy_cli.write_text("#!/bin/sh\nexit 0\n")
    return MagdaCliAdapter(dummy_cli, tmp_path / "proj.mgd")


# ---------------------------------------------------------------- layer 1 ----
def test_check_audible_flags_digital_silence():
    result = check_audible(-120.0)
    assert not result.passed
    assert "doctor" in result.detail  # points at the remedy


def test_check_audible_passes_realistic_levels():
    assert check_audible(-14.2).passed
    assert check_audible(-59.9, floor_dbfs=-60.0).passed
    assert not check_audible(-60.1, floor_dbfs=-60.0).passed


# ---------------------------------------------------------------- layer 2 ----
def test_guard_rejects_unknown_instrument(tmp_path):
    daw = _adapter(tmp_path)
    with pytest.raises(UnsupportedOperation):
        daw._guard_instrument("zzz-totally-bogus")


def test_guard_accepts_known_audible_instruments(tmp_path):
    daw = _adapter(tmp_path)
    for instrument_id in ("4osc", "magda_polysynth", "magda_kick"):
        daw._guard_instrument(instrument_id)  # must not raise


def test_registry_excludes_proven_silent_names():
    for silent in ("square", "pulse", "saw", "noise", "triangle", "sine"):
        assert silent not in KNOWN_AUDIBLE_INSTRUMENTS


def test_guard_prefers_doctor_manifest_over_builtin(tmp_path):
    """A fresh manifest is the source of truth even against the allowlist."""
    write_manifest(tmp_path, {
        "binary": "/tmp/some/magda_cli",
        "instruments": [{"id": "future_synth", "audible": True, "peak": 0.42}],
    })
    daw = _adapter(tmp_path)
    daw._guard_instrument("future_synth")  # manifest says audible: no raise
    with pytest.raises(UnsupportedOperation):
        daw._guard_instrument("4osc")  # ...but 4osc is unknown to THIS binary


def test_env_escape_hatch_bypasses_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPOSERBENCH_ALLOW_ANY_INSTRUMENT", "1")
    daw = _adapter(tmp_path)
    daw._guard_instrument("anything-at-all")  # must not raise


# ---------------------------------------------------------------- layer 3 ----
class _SilentRenderDaw(FakeDawAdapter):
    """A DAW whose engine 'works' but renders pure digital silence."""

    async def render(self, request):
        out = Path(request.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(b"".join(
                struct.pack("<h", 0) for _ in range(44100 // 2)))
        return out


async def test_revision_loop_auto_rejects_silent_render(tmp_path: Path):
    """The exact failure shipped on 2026-08-23 must now auto-REJECT."""
    daw = _SilentRenderDaw()

    def proposer(_world):
        return [
            Action(type="create_track", name="pads"),
            Action(type="load_device", track="pads", name="square"),  # silent!
            Action(type="create_clip", track="pads", name="c",
                   start_beats=0.0, length_beats=8.0),
            Action(type="add_notes", track="pads", clip="c",
                   notes=[Note(start=float(i), duration=1.0, pitch=60,
                               velocity=100) for i in range(8)]),
        ]

    loop = RevisionLoop(daw, PLAN, proposer, tmp_path,
                        budget=LoopBudget(max_iterations=1))
    history = await loop.run(["build something loud"])
    rec = history[0]
    assert rec.decision == "rejected", "silent render must never be accepted"
    failures = [c for c in rec.constraint_results
                if c["constraint"].startswith("peak>")]
    assert failures and all(c["status"] == "FAIL" for c in failures)


async def test_revision_loop_keeps_audible_render(tmp_path: Path):
    """Sanity: FakeDaw's audible sine render still passes the gate."""
    daw = FakeDawAdapter()

    def proposer(_world):
        return [
            Action(type="create_track", name="keys"),
            Action(type="create_clip", track="keys", name="c",
                   start_beats=0.0, length_beats=16.0),
            Action(type="add_notes", track="keys", clip="c",
                   notes=[Note(start=float(i), duration=0.5, pitch=72)
                          for i in range(16)]),
            Action(type="create_clip", track="keys", name="c2",
                   start_beats=16.0, length_beats=16.0),
            Action(type="add_notes", track="keys", clip="c2",
                   notes=[Note(start=16.0 + i * 0.25, duration=0.25,
                               pitch=72 + (i % 12), velocity=127)
                          for i in range(64)]),
        ]

    loop = RevisionLoop(daw, PLAN, proposer, tmp_path,
                        budget=LoopBudget(max_iterations=1))
    history = await loop.run(["arpeggio"])
    assert history[0].decision == "accepted"


def test_global_manifest_is_consulted(tmp_path, monkeypatch):
    # regression: a second definition of audible_instruments_for used to
    # shadow this one and silently skip the global manifest
    from composerbench.adapter import instruments
    manifest = tmp_path / "instrument_manifest.json"
    manifest.write_text('{"binary": "test", "instruments": [{"id": "Custom_Synth", "audible": true}]}')
    monkeypatch.setattr(instruments, "GLOBAL_MANIFEST", manifest)
    audible, source = instruments.audible_instruments_for(tmp_path / "project")
    assert audible == frozenset({"custom_synth"})
    assert source.startswith("global manifest")
