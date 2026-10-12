"""Note-writing helpers for agents: drum strings, chords, arps, slides, ramps.

Every helper works on plain note dicts -- ``{"start", "duration", "pitch",
"velocity"}`` with times in timeline-absolute beats -- which is exactly what
``music_notes_set`` and the song builder take, so results can be passed
straight through or saved as JSON.

Drum strings (``hits``), one character per step (16th notes by default)::

    x  hit            X  accent (x1.2)      o  ghost (x0.4)
    .  rest  -  rest  r  2-hit roll (32nds)  R  3-hit roll (16th triplets)
    3  6 hits spread over the next 4 steps (a beat of sextuplets)
    |  and spaces are ignored, so "x..x|..x.|" reads as bars/beats

    hits("x...x...x...x...", start=0, pitch=36)            # four on the floor
    hits("....x.......x...", start=16, pitch=38, velocity=110)
"""
from __future__ import annotations

import math
import random
import re
from typing import Iterable

Note = dict

NOTE_NAMES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
QUALITIES = {   # intervals above the root
    "": (0, 4, 7), "maj": (0, 4, 7), "M": (0, 4, 7), "m": (0, 3, 7), "min": (0, 3, 7), "-": (0, 3, 7),
    "5": (0, 7), "dim": (0, 3, 6), "aug": (0, 4, 8), "+": (0, 4, 8),
    "sus2": (0, 2, 7), "sus4": (0, 5, 7), "sus": (0, 5, 7),
    "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "M7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10), "min7": (0, 3, 7, 10), "mmaj7": (0, 3, 7, 11), "dim7": (0, 3, 6, 9),
    "m7b5": (0, 3, 6, 10), "7sus4": (0, 5, 7, 10), "add9": (0, 4, 7, 14), "madd9": (0, 3, 7, 14),
    "9": (0, 4, 7, 10, 14), "maj9": (0, 4, 7, 11, 14), "m9": (0, 3, 7, 10, 14), "11": (0, 4, 7, 10, 14, 17),
    "m11": (0, 3, 7, 10, 14, 17),
}
_CHORD_RE = re.compile(r"^([A-G])([#b]?)(.*?)(?:/([A-G])([#b]?))?$")


def note(start: float, duration: float, pitch: int, velocity: float = 100) -> Note:
    return {"start": round(float(start), 6), "duration": round(float(duration), 6),
            "pitch": int(pitch), "velocity": int(max(1, min(127, round(velocity))))}


def pitch_class(name: str) -> int:
    """'C#' -> 1, 'Bb' -> 10."""
    m = re.fullmatch(r"([A-Ga-g])([#b]?)", name.strip())
    if not m:
        raise ValueError(f"not a note name: {name!r}")
    pc = NOTE_NAMES[m.group(1).upper()]
    return (pc + (1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0)) % 12


def midi(name: str) -> int:
    """'C4' -> 60, 'F#2' -> 42, 'Bb-1' -> 10."""
    m = re.fullmatch(r"([A-Ga-g][#b]?)(-?\d+)", name.strip())
    if not m:
        raise ValueError(f"not a note: {name!r} (use e.g. 'C4', 'F#2')")
    return pitch_class(m.group(1)) + 12 * (int(m.group(2)) + 1)


def parse_chord(symbol: str) -> tuple[int, tuple[int, ...], int | None]:
    """'C#m7' -> (root pc, intervals, bass pc or None). Supports slash chords ('A/C#')."""
    m = _CHORD_RE.match(symbol.strip())
    if not m:
        raise ValueError(f"cannot parse chord {symbol!r}")
    root = pitch_class(m.group(1) + m.group(2))
    quality = m.group(3)
    if quality not in QUALITIES:
        raise ValueError(f"unknown chord quality {quality!r} in {symbol!r}; known: {sorted(QUALITIES)}")
    bass = pitch_class(m.group(4) + m.group(5)) if m.group(4) else None
    return root, QUALITIES[quality], bass


def chord_pitch_classes(symbol: str) -> set[int]:
    root, ivs, bass = parse_chord(symbol)
    pcs = {(root + i) % 12 for i in ivs}
    return pcs | ({bass} if bass is not None else set())


def chord_root(symbol: str, octave_floor: int = 28) -> int:
    """Lowest MIDI note >= octave_floor carrying the chord's bass (slash bass or root)."""
    root, _, bass = parse_chord(symbol)
    pc = bass if bass is not None else root
    return octave_floor + ((pc - octave_floor) % 12)


def voicing(symbol: str, low: int = 52, high: int = 76, size: int = 4,
            previous: list[int] | None = None) -> list[int]:
    """Close-ish voicing of a chord inside [low, high].

    With ``previous`` the voicing with the least total movement from it is chosen
    (simple voice leading); otherwise the lowest one that keeps the root or the
    3rd on top is used. ``size`` > number of chord tones doubles tones upward.
    """
    root, ivs, _ = parse_chord(symbol)
    pcs = [(root + i) % 12 for i in ivs]
    candidates = [p for p in range(low, high + 1) if p % 12 in pcs]
    if not candidates:
        raise ValueError(f"no chord tones of {symbol} in [{low}, {high}]")
    best, best_cost = None, math.inf
    for i in range(len(candidates)):
        v = [candidates[i]]
        j = i + 1
        used = {candidates[i] % 12}
        while len(v) < size and j < len(candidates):
            c = candidates[j]
            if c % 12 not in used or len(used) == len(set(pcs)):
                v.append(c)
                used.add(c % 12)
            j += 1
        if len(v) < size or len(used) < min(len(set(pcs)), size):
            continue
        if previous:
            cost = sum(min(abs(a - b) for b in previous) for a in v) + sum(
                min(abs(a - b) for a in v) for b in previous)
        else:
            cost = v[0] - low
        if cost < best_cost:
            best, best_cost = v, cost
    if best is None:
        raise ValueError(f"cannot fit a {size}-note {symbol} voicing in [{low}, {high}]")
    return best


def progression(symbols: Iterable[str], low: int = 52, high: int = 76, size: int = 4) -> list[list[int]]:
    """Voice-led voicings for a chord sequence."""
    out: list[list[int]] = []
    for s in symbols:
        out.append(voicing(s, low, high, size, out[-1] if out else None))
    return out


def hits(pattern: str, start: float, pitch: int, velocity: float = 100, step: float = 0.25,
         length: float | None = None, accent: float = 1.2, ghost: float = 0.4) -> list[Note]:
    """Drum-machine string -> notes (see module docstring for the symbols)."""
    out: list[Note] = []
    dur = length if length is not None else step * 0.8
    cells = [c for c in pattern if c not in " |"]
    for i, ch in enumerate(cells):
        t = start + i * step
        if ch in ".-_":
            continue
        if ch == "x":
            out.append(note(t, dur, pitch, velocity))
        elif ch == "X":
            out.append(note(t, dur, pitch, velocity * accent))
        elif ch == "o":
            out.append(note(t, dur, pitch, velocity * ghost))
        elif ch == "r":
            out += [note(t, step / 2 * 0.8, pitch, velocity * 0.85), note(t + step / 2, step / 2 * 0.8, pitch, velocity * 0.72)]
        elif ch == "R":
            out += [note(t + k * step / 3, step / 3 * 0.8, pitch, velocity * 0.8) for k in range(3)]
        elif ch == "3":
            out += [note(t + k * step * 4 / 6, step * 4 / 6 * 0.8, pitch, velocity * (0.95 - k * 0.06))
                    for k in range(6)]
        else:
            raise ValueError(f"unknown drum symbol {ch!r} in {pattern!r}")
    return out


def arpeggio(pitches: list[int], order: Iterable[int], start: float, step: float = 0.25,
             duration: float | None = None, velocity: float = 90, octaves: int = 2,
             accent_every: int = 0, accent: float = 1.15) -> list[Note]:
    """Arpeggiate chord tones. ``order`` indexes the sorted tones extended over
    ``octaves`` (index 0 = lowest); indexes wrap."""
    tones = sorted({p + 12 * o for o in range(octaves) for p in pitches})
    out = []
    for i, idx in enumerate(order):
        v = velocity * (accent if accent_every and i % accent_every == 0 else 1.0)
        out.append(note(start + i * step, duration if duration is not None else step * 0.9,
                        tones[idx % len(tones)], v))
    return out


def chord_notes(pitches: list[int], start: float, duration: float, velocity: float = 100) -> list[Note]:
    return [note(start, duration, p, velocity) for p in pitches]


def legato(notes: list[Note], slide: Iterable[bool] | None = None, overlap: float = 0.06,
           gap: float = 0.02) -> list[Note]:
    """Prepare a mono line for a Legato/Glide synth.

    Notes flagged in ``slide`` are extended to overlap the next note by
    ``overlap`` beats (the synth glides instead of retriggering); all other notes
    end ``gap`` beats before the next note starts (clean retrigger).
    """
    ns = sorted((dict(n) for n in notes), key=lambda n: n["start"])
    flags = list(slide) if slide is not None else [False] * len(ns)
    for i, n in enumerate(ns[:-1]):
        nxt = ns[i + 1]["start"]
        end = n["start"] + n["duration"]
        if i < len(flags) and flags[i]:
            n["duration"] = round(max(n["duration"], nxt - n["start"] + overlap), 6)
        elif end > nxt - gap:
            n["duration"] = round(max(0.01, nxt - gap - n["start"]), 6)
    return ns


def ramp(notes: list[Note], v0: float, v1: float, start: float | None = None,
         end: float | None = None, curve: float = 1.0) -> list[Note]:
    """Set velocities along a ramp over time (e.g. to sweep a Vel Filter open).
    ``curve`` > 1 eases in, < 1 eases out."""
    if not notes:
        return []
    t0 = min(n["start"] for n in notes) if start is None else start
    t1 = max(n["start"] for n in notes) if end is None else end
    out = []
    for n in notes:
        x = 0.0 if t1 <= t0 else min(1.0, max(0.0, (n["start"] - t0) / (t1 - t0)))
        out.append({**n, "velocity": int(max(1, min(127, round(v0 + (v1 - v0) * x ** curve))))})
    return out


def trim(notes: list[Note], windows: Iterable[tuple[float, float]], release: float = 0.02) -> list[Note]:
    """Silence windows (e.g. the dead beat before a drop): notes starting inside
    a window are dropped, notes ringing into one are shortened to end
    ``release`` beats before it."""
    wins = sorted((float(a), float(b)) for a, b in windows)
    out = []
    for n in notes:
        s, e = n["start"], n["start"] + n["duration"]
        if any(a <= s < b for a, b in wins):
            continue
        for a, _ in wins:
            if s < a < e:
                e = a - release
        if e - s > 0.005:
            out.append({**n, "duration": round(e - s, 6)})
    return out


def transpose(notes: list[Note], semitones: int) -> list[Note]:
    return [{**n, "pitch": n["pitch"] + semitones} for n in notes]


def shift(notes: list[Note], beats: float) -> list[Note]:
    return [{**n, "start": round(n["start"] + beats, 6)} for n in notes]


def humanize(notes: list[Note], timing: float = 0.0, velocity: float = 0.0, seed: int = 0) -> list[Note]:
    """Random timing (beats, +-) and velocity (+-) offsets, reproducible by seed."""
    rng = random.Random(seed)
    out = []
    for n in notes:
        dt = rng.uniform(-timing, timing) if timing else 0.0
        dv = rng.uniform(-velocity, velocity) if velocity else 0.0
        out.append({**n, "start": round(max(0.0, n["start"] + dt), 6),
                    "velocity": int(max(1, min(127, round(n["velocity"] + dv))))})
    return out


def repeat(notes: list[Note], every: float, times: int) -> list[Note]:
    """Copy a phrase ``times`` times, ``every`` beats apart."""
    return [n2 for k in range(times) for n2 in shift(notes, k * every)]


def stutter(pitches: list[int], start: float, length: float, slice_beats: float,
            velocity: float = 100, decay: float = 1.0, gate: float = 0.7) -> list[Note]:
    """Retriggered chord/note (a 'stutter edit' written as MIDI)."""
    out, t, k = [], start, 0
    while t < start + length - 1e-9:
        out += [note(t, slice_beats * gate, p, velocity * decay ** k) for p in pitches]
        t += slice_beats
        k += 1
    return out
