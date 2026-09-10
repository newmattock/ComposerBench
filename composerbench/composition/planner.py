"""Section planner (Sprint D2): intent + seed motif -> arrangement schedule.

Proposes per-section transformation schedules referencing stored motifs.
Proposals are still validated by the RevisionLoop's constraints + critic.
"""
from __future__ import annotations

from composerbench.composition.state import CompositionPlan, Section
from composerbench.composition.transforms import (
    fragment, octave_lift, thin, velocity_shape,
)
from composerbench.model import Note


class ArrangementStep:
    """One transformed placement: motif -> transform chain -> track/section."""

    def __init__(self, section: str, track: str, notes: list[Note],
                 transforms: list[str]):
        self.section = section
        self.track = track
        self.notes = notes
        self.transforms = transforms

    def __repr__(self):
        return (f"<{self.track}@{self.section} "
                f"{'.'.join(self.transforms) or 'seed'} n={len(self.notes)}>")


# Heuristic schedule templates keyed on arc shape (rising then falling)
ARC_TEMPLATES = {
    # 4 sections rising to glow then drift:
    "rise_fall": [
        ("fragment(3)", []),                    # dawn: state the head
        ("velocity_shape(rise)", []),           # bloom: full theme, building
        ("octave_lift+thin", [0.6]),            # glow: bright echo, thinned
        ("fragment(2)+sustain-ish", None),      # drift: remembrance
    ],
}


def plan_arrangement(seed_notes: list[Note], plan: CompositionPlan,
                     lead_track: str = "lead",
                     answer_track: str | None = "bells") -> list[ArrangementStep]:
    """Derive an arrangement schedule from a plan's energy arc.

    Uses transformation chains per section; places answers on the answer
    track when provided. Returns steps in section order.
    """
    steps: list[ArrangementStep] = []
    sections = plan.sections
    for i, sec in enumerate(sections):
        target = sec.target_energy
        if i == 0 or target < 0.35:
            head = fragment(seed_notes, 3)
            steps.append(ArrangementStep(sec.name, lead_track, head,
                                         ["fragment"]))
        elif target >= max(s.target_energy for s in sections) - 0.05 and \
                answer_track and i < len(sections) - 1:
            bright = velocity_shape(octave_lift(thin(seed_notes, 0.6)), "rise")
            shifted = [Note(start=n.start + 64.0 * 0 + (sec.start_bar - 1) * plan.beats_per_bar,
                            duration=n.duration, pitch=n.pitch,
                            velocity=n.velocity) for n in bright]
            steps.append(ArrangementStep(sec.name, answer_track, shifted,
                                         ["octave_lift", "thin",
                                          "velocity_shape(rise)"]))
        elif target >= 0.55:
            built = velocity_shape(list(seed_notes), "rise")
            shifted = [Note(start=n.start + (sec.start_bar - 1) * plan.beats_per_bar,
                            duration=n.duration, pitch=n.pitch,
                            velocity=n.velocity) for n in built]
            steps.append(ArrangementStep(sec.name, lead_track, shifted,
                                         ["velocity_shape(rise)"]))
        else:
            ember = fragment(octave_lift(seed_notes[:2]), 2)
            steps.append(ArrangementStep(sec.name, answer_track or lead_track,
                                         ember, ["fragment", "octave_lift"]))
    return steps


def _slot_offset(section: Section) -> float:
    return float((section.start_bar - 1) * 4)
