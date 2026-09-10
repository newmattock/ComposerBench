from pathlib import Path

from composerbench.adapter import FakeDawAdapter
from composerbench.composition.state import CompositionPlan, Section, SidecarStore
from composerbench.evaluation.constraints import (
    check_energy_relation,
    check_no_vocals,
    check_section_count,
    check_track_present,
    section_energy,
)
from composerbench.model import Note


def _dense_pattern() -> list[Note]:
    return [Note(start=i * 0.25, duration=0.25, pitch=45 + (i % 12), velocity=127) for i in range(64)]


def _sparse_pattern() -> list[Note]:
    # Exactly fills one 4-bar / 16-beat section without spilling over.
    return [Note(start=float(i * 4), duration=2.0, pitch=60, velocity=50) for i in range(4)]


async def build_project():
    daw = FakeDawAdapter()
    plan = CompositionPlan(
        sections=[Section(name="intro", bars=4, start_bar=1, target_energy=0.15),
                  Section(name="climax", bars=4, start_bar=5, target_energy=0.9)]
    )
    piano = await daw.create_track("piano")
    clip_intro = await daw.create_clip(piano.id, "intro", 0.0, 16.0)
    await daw.add_notes(
        piano.id, clip_intro.id,
        [Note(start=n.start, duration=n.duration, pitch=n.pitch, velocity=n.velocity)
         for n in _sparse_pattern()],
    )
    clip_climax = await daw.create_clip(piano.id, "climax", 16.0, 16.0)
    await daw.add_notes(
        piano.id, clip_climax.id,
        [Note(start=16.0 + n.start, duration=n.duration, pitch=n.pitch, velocity=n.velocity)
         for n in _dense_pattern()],
    )
    return daw, plan


def test_sidecar_roundtrip(tmp_path: Path):
    store = SidecarStore(tmp_path)
    plan = CompositionPlan(sections=[Section(name="verse", bars=8, start_bar=1)], tempo_bpm=96.0)
    store.write_plan(plan)
    loaded = store.read_plan()
    assert loaded.tempo_bpm == 96.0
    assert loaded.section("verse").bars == 8


def test_intent_roundtrip(tmp_path: Path):
    from composerbench.composition.state import Intent

    store = SidecarStore(tmp_path)
    intent = Intent(title="test", goal="become enormous", principles=["keep motif"])
    store.write_intent(intent)
    loaded = store.read_intent()
    assert loaded.title == "test" and loaded.principles == ["keep motif"]


def test_plan_bar_range_to_beats():
    plan = CompositionPlan(sections=[Section(name="a", bars=8, start_bar=1)])
    assert plan.bar_range_to_beats(1, 8) == (0.0, 32.0)
    assert plan.bar_range_to_beats(9, 16) == (32.0, 32.0)


async def test_energy_relation_pass_and_fail():
    daw, plan = await build_project()
    notes_by_track = {"piano": await daw.get_notes("track:piano")}
    ok = check_energy_relation(notes_by_track, plan, lhs="climax", rhs="intro")
    assert ok.passed and ok.to_dict()["status"] == "PASS"
    bad = check_energy_relation(notes_by_track, plan, lhs="intro", rhs="climax")
    assert not bad.passed


async def test_section_energy_values_bounded():
    daw, plan = await build_project()
    notes_by_track = {"piano": await daw.get_notes("track:piano")}
    e_climax, stats_climax = section_energy(notes_by_track, plan, "climax")
    e_intro, _ = section_energy(notes_by_track, plan, "intro")
    assert 0.0 <= e_intro < e_climax <= 1.0
    assert stats_climax.note_count == 64


async def test_track_present_constraint():
    daw, plan = await build_project()
    notes_by_track = {"piano": await daw.get_notes("track:piano")}
    present = check_track_present(notes_by_track, plan, "climax", "piano")
    absent = check_track_present({"drums": []}, plan, "climax", "drums")
    assert present.passed
    assert not absent.passed


def test_no_vocals_constraint():
    assert check_no_vocals(["piano", "bass", "drums"]).passed
    assert not check_no_vocals(["lead vocal", "pad"]).passed


def test_section_count_constraint():
    plan = CompositionPlan(sections=[Section(name="only", bars=4, start_bar=1)])
    assert not check_section_count(plan, minimum=2).passed
    assert check_section_count(plan, minimum=1).passed
