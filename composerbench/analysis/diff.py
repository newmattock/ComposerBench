"""Semantic musical diff (spec §26).

Compares two project states *as music*, not bytes: per-track symbolic
deltas so an agent understands WHAT changed and can attribute causes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from composerbench.analysis.symbolic import SymbolicStats, analyze_notes
from composerbench.model import Note


@dataclass
class TrackDiff:
    track_id: str
    before: SymbolicStats
    after: SymbolicStats
    notes_added: int = 0
    notes_removed: int = 0

    def summary_lines(self) -> list[str]:
        b, a = self.before, self.after
        lines = [f"{self.track_id}:"]
        if b.note_count == 0 and a.note_count > 0:
            lines.append(f"  + {a.note_count} new notes (was silent)")
            return lines
        density_delta = _pct(b.density_per_bar, a.density_per_bar)
        if density_delta is not None:
            lines.append(f"  note density {density_delta}")
        if b.median_pitch is not None and a.median_pitch is not None:
            shift = a.median_pitch - b.median_pitch
            if abs(shift) >= 1:
                lines.append(f"  median pitch {shift:+.0f} semitones")
        vel_delta = (
            f"{(a.velocity_mean - b.velocity_mean):+.1f}"
            if b.velocity_mean is not None and a.velocity_mean is not None else None
        )
        if vel_delta:
            lines.append(f"  mean velocity {vel_delta}")
        if self.notes_added:
            lines.append(f"  {self.notes_added} notes added")
        if self.notes_removed:
            lines.append(f"  {self.notes_removed} notes removed")
        if len(lines) == 1:
            lines.append("  unchanged")
        return lines


@dataclass
class ProjectDiff:
    tracks: dict[str, TrackDiff] = field(default_factory=dict)

    def summary(self) -> str:
        lines = ["Revision diff:"]
        for td in self.tracks.values():
            lines.extend(td.summary_lines())
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            tid: {
                "before": td.before.to_dict(),
                "after": td.after.to_dict(),
                "notes_added": td.notes_added,
                "notes_removed": td.notes_removed,
            }
            for tid, td in self.tracks.items()
        }


def _pct(before: float, after: float) -> str | None:
    if before == 0:
        return None
    delta = (after - before) / before * 100.0
    return f"{delta:+.0f}%" if abs(delta) >= 1 else None


def diff_tracks(
    before: dict[str, list[Note]],
    after: dict[str, list[Note]],
) -> ProjectDiff:
    all_ids = set(before) | set(after)
    result = ProjectDiff()
    for tid in sorted(all_ids):
        b_notes = before.get(tid, [])
        a_notes = after.get(tid, [])
        span = max(
            max((n.start + n.duration for n in b_notes), default=0.0),
            max((n.start + n.duration for n in a_notes), default=0.0),
            1e-9,
        )
        result.tracks[tid] = TrackDiff(
            track_id=tid,
            before=analyze_notes(b_notes, span_beats=span),
            after=analyze_notes(a_notes, span_beats=span),
            notes_added=max(0, len(a_notes) - len(b_notes)),
            notes_removed=max(0, len(b_notes) - len(a_notes)),
        )
    return result
