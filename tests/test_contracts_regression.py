"""Regression tests for contracts that broke once in live runs.

1. Coordinate contract: get_notes/get_clips return TIMELINE-ABSOLUTE starts
   even though MAGDA stores clip-relative note positions.
2. Peak constraint flags clipping renders (the beat-16 boundary click).
"""

from pathlib import Path

import pytest

from composerbench.adapter import FakeDawAdapter
from composerbench.evaluation.constraints import check_peak_below
from composerbench.analysis.audio import AudioStats
from composerbench.model import Note

MAGDA_CLI = Path(
    "~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli"
).expanduser()

pytestmark_magda = pytest.mark.skipif(
    not MAGDA_CLI.exists(), reason="magda-cli binary not built yet"
)


async def test_fake_notes_are_timeline_absolute(tmp_path):
    daw = FakeDawAdapter(state_file=tmp_path / "s.json")
    t = await daw.create_track("p")
    await daw.create_clip(t.id, "late", start=16.0, length=8.0)
    # Action notes are timeline-absolute; clip covers [16, 24).
    await daw.add_notes(t.id, "clip:late", [Note(start=16.0, duration=1.0, pitch=40)])
    notes = await daw.get_notes(t.id)
    assert notes[0].start == pytest.approx(16.0)


@pytest.mark.skipif(not MAGDA_CLI.exists(), reason="magda-cli not built")
async def test_magda_projects_absolute_coordinates(tmp_path):
    """A note added at absolute beat 20 into a clip at 16 must read back
    as 20 (absolute), not 4 (clip-relative). This exact mismatch made the
    real revision loop measure climax energy as 0.00."""
    import asyncio

    from composerbench.adapter.magda import MagdaCliAdapter

    daw = MagdaCliAdapter(MAGDA_CLI, tmp_path / "proj.mgd")
    await daw.initialize()
    t = await daw.create_track("bass")
    await daw.load_device(t.id, "4osc")
    await daw.create_clip(t.id, "climax", start=16.0, length=8.0)
    await daw.add_notes(t.id, "clip:climax", [Note(start=20.0, duration=1.0, pitch=40)])
    notes = await daw.get_notes(t.id)
    assert len(notes) == 1
    assert notes[0].start == pytest.approx(20.0, abs=1e-6)

    from composerbench.composition.state import CompositionPlan, Section
    from composerbench.evaluation.constraints import section_energy

    plan = CompositionPlan(sections=[Section(name="a", bars=2, start_bar=5)])
    energy, stats = section_energy({"bass": notes}, plan, "a")
    assert stats.note_count == 1
    assert energy > 0


def test_peak_constraint_flags_clipping():
    ok = check_peak_below(-3.0, limit_dbfs=-0.5)
    clipped = check_peak_below(0.0, limit_dbfs=-0.5)
    assert ok.passed
    assert not clipped.passed and "CLIPPING" in clipped.detail


def test_loop_rejects_render_when_peak_exceeds_limit(tmp_path):
    """Wire check through RevisionLoop: a max-peak limit turns a clipping
    render into a rejected revision with restored state."""
    import asyncio

    from composerbench.agent.loop import Action, RenderConstraints, RevisionLoop
    from composerbench.composition.state import CompositionPlan, Section

    daw = FakeDawAdapter()

    def proposer(_w):
        return [
            Action(type="create_track", name="g"),
            Action(type="create_clip", track="g", name="c",
                   start_beats=0.0, length_beats=8.0),
            Action(type="add_notes", track="g", clip="c",
                   notes=[Note(start=i, duration=1, pitch=60) for i in range(8)]),
        ]

    plan = CompositionPlan(sections=[Section(name="only", bars=2, start_bar=1)])
    loop = RevisionLoop(
        daw, plan, proposer, tmp_path,
        render_audio=True,
        render_constraints=RenderConstraints(max_peak_dbfs=-30.0),
    )
    history = asyncio.run(loop.run(["make sound"]))
    rec = history[0]
    assert rec.decision == "rejected"
    assert any("peak" in c["constraint"] for c in rec.constraint_results)
