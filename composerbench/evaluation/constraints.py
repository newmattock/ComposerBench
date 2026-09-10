"""Declarative constraint validation (spec §33).

Validators return PASS/FAIL records with human-readable reasons so agents
can act on them. Deterministic checks only; subjective judgment lives in
the critic layer, never here.
"""

from __future__ import annotations

from dataclasses import dataclass

from composerbench.analysis.energy import estimate_energy
from composerbench.analysis.symbolic import SymbolicStats, analyze_notes
from composerbench.composition.state import CompositionPlan
from composerbench.errors import EntityNotFound
from composerbench.model import Note


@dataclass
class ConstraintResult:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return {"constraint": self.name, "status": "PASS" if self.passed else "FAIL", "detail": self.detail}


def _section_notes(
    notes_by_track: dict[str, list[Note]],
    plan: CompositionPlan,
    section_name: str,
) -> list[Note]:
    section = plan.section(section_name)
    if section is None:
        raise EntityNotFound("section", section_name, [s.name for s in plan.sections])
    start, length = plan.bar_range_to_beats(section.start_bar, section.end_bar)
    out = []
    for notes in notes_by_track.values():
        out.extend(n for n in notes if start <= n.start < start + length)
    return out


def section_energy(
    notes_by_track: dict[str, list[Note]],
    plan: CompositionPlan,
    section_name: str,
) -> tuple[float, SymbolicStats]:
    notes = _section_notes(notes_by_track, plan, section_name)
    section = plan.section(section_name)
    span = plan.beats_per_bar * (section.bars or 0)
    window_start, _ = plan.bar_range_to_beats(section.start_bar, section.end_bar)
    stats = analyze_notes(notes, span_beats=float(span), start_beat=float(window_start))
    return estimate_energy(stats), stats


def check_energy_relation(
    notes_by_track: dict[str, list[Note]],
    plan: CompositionPlan,
    lhs: str,
    rhs: str,
) -> ConstraintResult:
    e_lhs, _ = section_energy(notes_by_track, plan, lhs)
    e_rhs, _ = section_energy(notes_by_track, plan, rhs)
    return ConstraintResult(
        name=f"energy:{lhs}>{rhs}",
        passed=e_lhs > e_rhs,
        detail=f"energy({lhs})={e_lhs:.2f} vs energy({rhs})={e_rhs:.2f}",
    )


def check_track_present(
    track_names: list[str],
    plan: CompositionPlan,
    section_name: str,
    expected_track: str,
) -> ConstraintResult:
    section = plan.section(section_name)
    start, length = plan.bar_range_to_beats(section.start_bar, section.end_bar)
    active = set()
    for name, notes in track_names.items():
        if any(start <= n.start < start + length for n in notes):
            active.add(name)
    return ConstraintResult(
        name=f"track_present:{expected_track}@{section_name}",
        passed=expected_track in active,
        detail=f"active tracks in {section_name}: {sorted(active)}",
    )


def check_no_vocals(track_names: list[str]) -> ConstraintResult:
    vocal_hints = ("vocal", "voice", "choir", "singer")
    offenders = [t for t in track_names if any(h in t.lower() for h in vocal_hints)]
    return ConstraintResult(
        name="no_vocals",
        passed=not offenders,
        detail="vocal-like tracks found" if offenders else "no vocal tracks detected",
    )


def check_section_count(plan: CompositionPlan, minimum: int) -> ConstraintResult:
    return ConstraintResult(
        name=f"sections>={minimum}",
        passed=len(plan.sections) >= minimum,
        detail=f"{len(plan.sections)} sections planned",
    )


def check_peak_below(peak_dbfs: float, limit_dbfs: float) -> ConstraintResult:
    """Sample-peak guard over rendered audio (spec §33 maximum_peak)."""
    return ConstraintResult(
        name=f"peak<={limit_dbfs}dBFS",
        passed=peak_dbfs <= limit_dbfs,
        detail=f"rendered peak {peak_dbfs:.2f} dBFS vs limit {limit_dbfs} dBFS"
        + (" — CLIPPING LIKELY" if peak_dbfs >= -0.1 else ""),
    )


def check_audible(peak_dbfs: float, floor_dbfs: float = -60.0) -> ConstraintResult:
    """Silence gate over rendered audio.

    MIDI renders at -120 dBFS when no audible instrument is loaded (verified
    live against magda-core @ ebd93db0). A render this quiet is never an
    artistic choice - it is a broken device load. Failing this constraint
    rejects the revision and restores the snapshot.
    """
    passed = peak_dbfs > floor_dbfs
    hint = (
        " (no audible instrument loaded? run `composerbench doctor`)"
        if not passed
        else ""
    )
    return ConstraintResult(
        name=f"peak>{floor_dbfs}dBFS",
        passed=passed,
        detail=f"rendered peak {peak_dbfs:.2f} dBFS vs silence floor {floor_dbfs} dBFS"
        + hint,
    )
