"""First-class motif tracking (spec §16).

A motif is ComposerBench state: source notes, recorded appearances, and
transformations. Occurrence search is transposition-invariant (matches on
interval contours, not absolute pitch), which is what makes "keep bringing
the melody back, transformed each time" verifiable rather than aspirational.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from composerbench.model import Note


@dataclass
class Appearance:
    appearance_id: str
    motif_id: str
    track: str
    transformation: str          # original | transpose | invert | augment | ...
    semitones: int = 0           # for transpose/invert bookkeeping
    notes: list[Note] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "appearance_id": self.appearance_id,
            "motif_id": self.motif_id,
            "track": self.track,
            "transformation": self.transformation,
            "semitones": self.semitones,
            "notes": [n.model_dump() for n in self.notes],
        }


def _intervals(notes: list[Note]) -> list[int]:
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    return [b.pitch - a.pitch for a, b in zip(ordered, ordered[1:])]


def _contour_match(haystack: list[int], needle: list[int]) -> bool:
    """True if needle's interval sequence appears anywhere in haystack."""
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def find_occurrences(motif_notes: list[Note], candidates: list[Note]) -> list[Note]:
    """Notes in `candidates` whose melodic contour continues the motif's
    interval sequence (transposition-invariant). Returns every note of each
    matched run; non-overlapping, leftmost-first."""
    motif_iv = _intervals(motif_notes)
    if len(motif_iv) < 2:
        return []
    ordered = sorted(candidates, key=lambda n: (n.start, n.pitch))
    cand_iv = [b.pitch - a.pitch for a, b in zip(ordered, ordered[1:])]
    hits: list[Note] = []
    i = 0
    while i <= len(cand_iv) - len(motif_iv):
        if cand_iv[i:i + len(motif_iv)] == motif_iv:
            hits.extend(ordered[i:i + len(motif_iv) + 1])
            i += len(motif_iv)
        else:
            i += 1
    return hits


def transpose(notes: list[Note], semitones: int) -> list[Note]:
    return [n.model_copy(update={"pitch": max(0, min(127, n.pitch + semitones))})
            for n in notes]


def invert(notes: list[Note], around: int | None = None) -> list[Note]:
    axis = around if around is not None else notes[0].pitch
    return [n.model_copy(update={"pitch": max(0, min(127, 2 * axis - n.pitch))})
            for n in notes]


def augment(notes: list[Note], factor: float, origin: float | None = None) -> list[Note]:
    o = origin if origin is not None else notes[0].start
    return [
        n.model_copy(update={"start": o + (n.start - o) * factor,
                             "duration": n.duration * factor})
        for n in notes
    ]


class MotifStore:
    """Persists motif definitions and their appearance lineage."""

    def __init__(self, sidecar_dir: Path | str):
        self.path = Path(sidecar_dir) / ".musicbench" / "motifs.json"
        self.appearances: dict[str, Appearance] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        self.appearances = {
            k: Appearance(
                appearance_id=v["appearance_id"],
                motif_id=v["motif_id"],
                track=v["track"],
                transformation=v["transformation"],
                semitones=v.get("semitones", 0),
                notes=[Note.model_validate(n) for n in v["notes"]],
            )
            for k, v in data.items()
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {k: a.to_dict() for k, a in self.appearances.items()}, indent=1))

    def record(
        self,
        motif_id: str,
        track: str,
        notes: list[Note],
        transformation: str = "original",
        semitones: int = 0,
    ) -> Appearance:
        n_existing = sum(1 for a in self.appearances.values() if a.motif_id == motif_id)
        app = Appearance(
            appearance_id=f"{motif_id}#{n_existing}",
            motif_id=motif_id,
            track=track,
            transformation=transformation,
            semitones=semitones,
            notes=list(notes),
        )
        self.appearances[app.appearance_id] = app
        self.save()
        return app

    def occurrences_in(
        self, motif_id: str, candidates: list[Note]
    ) -> list[Appearance]:
        """Record every place `candidates` restate the motif's contour."""
        apps = [a for a in self.appearances.values() if a.motif_id == motif_id]
        found: list[Appearance] = []
        for src in apps[:1] or []:
            for hit in find_occurrences(src.notes, candidates):
                dup = any(
                    h.start == hit.start and h.pitch == hit.pitch
                    for a in self.appearances.values() for h in a.notes
                )
                if not dup:
                    found.append(Appearance(
                        appearance_id=f"{motif_id}-hit",
                        motif_id=motif_id,
                        track="?",
                        transformation="recurrence",
                        notes=[hit],
                    ))
        return found

    def lineage(self, motif_id: str) -> list[str]:
        out = []
        for a in sorted(self.appearances.values(), key=lambda x: x.appearance_id):
            if a.motif_id == motif_id:
                label = a.transformation
                if a.semitones:
                    label += f" ({a.semitones:+d}st)"
                out.append(f"{a.appearance_id}: {label} on {a.track}")
        return out
