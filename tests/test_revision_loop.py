from pathlib import Path

from composerbench.adapter import FakeDawAdapter
from composerbench.agent.loop import Action, LoopBudget, RevisionLoop
from composerbench.analysis.audio import analyze_wav
from composerbench.composition.state import CompositionPlan, Section
from composerbench.model import Note

PLAN = CompositionPlan(
    sections=[Section(name="intro", bars=4, start_bar=1, target_energy=0.2),
              Section(name="climax", bars=4, start_bar=5, target_energy=0.9)]
)


async def test_accepted_revision_is_kept_and_rendered(tmp_path: Path):
    daw = FakeDawAdapter()

    def proposer(world):
        return [
            Action(type="create_track", name="piano"),
            Action(type="create_clip", track="piano", name="intro",
                   start_beats=0.0, length_beats=16.0),
            Action(type="add_notes", track="piano", clip="intro",
                   notes=[Note(start=float(i * 4), duration=2.0, pitch=60, velocity=60)
                          for i in range(4)]),
            Action(type="create_clip", track="piano", name="climax",
                   start_beats=16.0, length_beats=16.0),
            Action(type="add_notes", track="piano", clip="climax",
                   notes=[Note(start=16.0 + i * 0.25, duration=0.25,
                               pitch=45 + (i % 12), velocity=127)
                          for i in range(64)]),
        ]

    loop = RevisionLoop(daw, PLAN, proposer, tmp_path)
    history = await loop.run(["build two-section arc"])
    assert len(history) == 1
    rec = history[0]
    assert rec.decision == "accepted"
    assert rec.render_path is not None and Path(rec.render_path).exists()
    assert any(c["status"] == "PASS" and "energy" in c["constraint"]
               for c in rec.constraint_results)
    assert "piano" in rec.diff_summary


async def test_rejected_revision_reverts_state(tmp_path: Path):
    daw = FakeDawAdapter()

    async def seed(_):
        t = await daw.create_track("vocal lead")
        clip = await daw.create_clip(t.id, "c1", 0.0, 16.0)
        await daw.add_notes(t.id, clip.id,
                            [Note(start=float(i * 4), duration=2.0, pitch=60) for i in range(4)])

    await seed(None)

    def bad_proposer(world):
        return [Action(type="create_track", name="choir backing")]

    loop = RevisionLoop(daw, PLAN, bad_proposer, tmp_path, render_audio=False)
    history = await loop.run(["add vocals"])  # no_vocals constraint must fail this
    rec = history[0]
    assert rec.decision == "rejected"
    names = [t.name for t in await daw.get_tracks()]
    assert "choir backing" not in names          # state reverted
    assert "vocal lead" in names                 # original preserved


async def test_budget_limits_iterations(tmp_path: Path):
    daw = FakeDawAdapter()
    calls = {"n": 0}

    def proposer(_world):
        calls["n"] += 1
        return []

    loop = RevisionLoop(daw, PLAN, proposer, tmp_path,
                        budget=LoopBudget(max_iterations=3), render_audio=False)
    history = await loop.run(["a", "b", "c", "d", "e"])
    assert calls["n"] == 3 and len(history) == 3


async def test_history_records_causal_diff(tmp_path: Path):
    daw = FakeDawAdapter()
    # Single-section plan: this test exercises diff attribution,
    # not the cross-section energy constraint.
    plan = CompositionPlan(sections=[Section(name="groove", bars=8, start_bar=1)])

    def step1(world):
        if not world["notes_by_track"]:
            return [
                Action(type="create_track", name="bass"),
                Action(type="create_clip", track="bass", name="line",
                       start_beats=0.0, length_beats=16.0),
                Action(type="add_notes", track="bass", clip="line",
                       notes=[Note(start=i, duration=1, pitch=40) for i in range(8)]),
            ]
        return [Action(type="transpose_clip", track="bass", semitones=5)]

    loop = RevisionLoop(daw, plan, step1, tmp_path, render_audio=False)
    history = await loop.run(["write bass", "lift it"])
    assert all(r.decision == "accepted" for r in history)
    assert "median pitch +5 semitones" in history[1].diff_summary


async def test_render_analysis_roundtrip(tmp_path: Path):
    """Rendered audio from an accepted revision is measurable (§80 partial)."""
    daw = FakeDawAdapter()
    daw.tempo = getattr(daw, "_tempo", 120.0)

    def proposer(_w):
        return [
            Action(type="create_track", name="keys"),
            Action(type="create_clip", track="keys", name="c1",
                   start_beats=0.0, length_beats=16.0),
            Action(type="add_notes", track="keys", clip="c1",
                   notes=[Note(start=float(i), duration=0.5, pitch=72) for i in range(16)]),
        ]

    loop = RevisionLoop(daw, PLAN, proposer, tmp_path)
    history = await loop.run(["arpeggio"])
    stats = analyze_wav(history[0].render_path)
    assert stats.duration_seconds == pytest.approx(8.0, abs=0.2)


import pytest  # noqa: E402
