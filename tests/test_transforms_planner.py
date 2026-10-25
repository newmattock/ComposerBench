"""Transforms + planner tests (Sprint D1/D2)."""

from composerbench.composition.planner import plan_arrangement
from composerbench.composition.state import CompositionPlan, Section
from composerbench.composition.transforms import (
    octave_lift,
    thin,
    transpose,
    velocity_shape,
)
from composerbench.model import Note


def _seed():
    return [Note(start=0, duration=1, pitch=78, velocity=90),
            Note(start=1, duration=.5, pitch=81, velocity=90),
            Note(start=1.5, duration=1.5, pitch=78, velocity=90),
            Note(start=3.5, duration=.5, pitch=74, velocity=90),
            Note(start=4, duration=2, pitch=73, velocity=90)]


def test_transpose_clamps():
    out = transpose(_seed(), 12)
    assert out[0].pitch == 90
    low = transpose([Note(start=0, duration=1, pitch=2, velocity=80)], 12)
    assert low[0].pitch == 14


def test_thin_keeps_order():
    seed = _seed()
    out = thin(seed, 0.5)
    assert [n.pitch for n in out] == [n.pitch for n in seed[::2]]


def test_octave_lift_composes_transpose():
    seed = _seed()
    a = octave_lift(seed, 2)
    b = transpose(transpose(seed, 12), 12)
    assert [n.pitch for n in a] == [n.pitch for n in b]


def test_velocity_shape_arc_peaks_midway():
    out = velocity_shape(_seed() * 4, "arc")
    mids = [n.velocity for n in out]
    assert max(mids) >= mids[len(mids) // 2] >= mids[0]


def test_plan_arrangement_shapes_four_sections():
    plan = CompositionPlan(
        tempo_bpm=110, key="D major",
        sections=[
            Section(name="dawn", bars=8, start_bar=1, target_energy=0.42),
            Section(name="bloom", bars=8, start_bar=9, target_energy=0.60),
            Section(name="glow", bars=8, start_bar=17, target_energy=0.80),
            Section(name="drift", bars=8, start_bar=25, target_energy=0.28),
        ])
    steps = plan_arrangement(_seed(), plan)
    by_sec = {s.section: s for s in steps}
    # dawn = fragment head
    assert len(by_sec["dawn"].notes) < len(_seed())
    # glow = bright thinned answer on bells
    assert by_sec["glow"].track == "bells"
    glow_pitches = [n.pitch for n in by_sec["glow"].notes]
    assert min(glow_pitches) > 84      # lifted +12 from the 72-84 seed
    # drift = remembrance fragment (head only)
    assert len(by_sec["drift"].notes) < len(_seed())
