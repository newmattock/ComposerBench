"""Motif transformation library (Sprint D1).

Pure note-in/note-out functions; each returns NEW notes, never mutates.
All take `start` offsets so planners can place transformed material freely.
"""
from __future__ import annotations

from composerbench.model import Note


def transpose(notes: list[Note], semitones: int) -> list[Note]:
    return [Note(start=n.start, duration=n.duration,
                 pitch=max(0, min(127, n.pitch + semitones)),
                 velocity=n.velocity) for n in notes]


def thin(notes: list[Note], keep: float = 0.6) -> list[Note]:
    """Keep every ceil(1/keep)-th note (order-stable)."""
    if keep >= 1.0:
        return list(notes)
    step = max(2, round(1.0 / max(keep, 0.05)))
    return notes[::step]


def octave_lift(notes: list[Note], octaves: int = 1) -> list[Note]:
    return transpose(notes, 12 * octaves)


def call_response(notes: list[Note], gap_beats: float = 2.0,
                  shift_semitones: int = 0) -> tuple[list[Note], list[Note]]:
    """Split into call (original) and response (shifted, after a rest)."""
    end = max((n.start + n.duration for n in notes), default=0.0)
    response = [Note(start=n.start + end + gap_beats,
                     duration=n.duration,
                     pitch=max(0, min(127, n.pitch + shift_semitones)),
                     velocity=max(40, n.velocity - 10)) for n in notes]
    return list(notes), response


def fragment(notes: list[Note], count: int = 3) -> list[Note]:
    """Phrase head only — first `count` notes."""
    return notes[:max(count, 1)]


def sustain(notes: list[Note], mult: float = 1.5) -> list[Note]:
    """Stretch durations (and inter-onset gaps proportionally)."""
    if not notes:
        return []
    scale = float(mult)
    out = []
    origin = min(n.start for n in notes)
    for n in notes:
        rel = n.start - origin
        out.append(Note(start=origin + rel * scale, duration=n.duration * scale,
                        pitch=n.pitch, velocity=n.velocity))
    return out


def velocity_shape(notes: list[Note], curve: str = "arc",
                   base: int = 80, span: int = 40) -> list[Note]:
    """Shape velocities across the phrase. arc|rise|fall."""
    n_count = len(notes)
    if not n_count:
        return []
    out = []
    for i, n in enumerate(notes):
        t = i / max(n_count - 1, 1)
        if curve == "rise":
            f = t
        elif curve == "fall":
            f = 1.0 - t
        else:   # arc
            f = math_sin_pi(t)
        vel = max(20, min(127, int(base + span * f)))
        out.append(Note(start=n.start, duration=n.duration, pitch=n.pitch,
                        velocity=vel))
    return out


def math_sin_pi(x: float) -> float:
    import math
    return math.sin(math.pi * x)


TRANSFORMS = {
    "transpose": transpose,
    "thin": thin,
    "octave_lift": octave_lift,
    "fragment": fragment,
    "sustain": sustain,
    "velocity_shape": velocity_shape,
}
