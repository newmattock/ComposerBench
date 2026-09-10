"""Symbolic analysis over MIDI state (spec §29).

These metrics are deliberately approximate: their job is to give agents
useful feedback loops, not perfect musicology. Deterministic by design.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from composerbench.model import Note


@dataclass
class SymbolicStats:
    """Symbolic measurements for a set of notes spanning a beat range."""

    note_count: int = 0
    span_beats: float = 0.0
    density_per_bar: float = 0.0          # notes / bar (bar = 4 beats)
    onsets_per_bar: float = 0.0
    pitch_min: int | None = None
    pitch_max: int | None = None
    pitch_range: int = 0                  # max - min (0 when empty)
    median_pitch: float | None = None
    velocity_mean: float | None = None
    max_polyphony: int = 0
    rhythmic_entropy: float = 0.0         # normalized 0..1 over a 16th-note grid
    syncopation: float = 0.0              # fraction of onsets off integer beats
    silence_ratio: float = 0.0            # fraction of span without sounding notes
    repetition_ratio: float = 0.0         # recurring 3-grams of melodic intervals
    pitch_class_histogram: dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "note_count": self.note_count,
            "span_beats": round(self.span_beats, 3),
            "density_per_bar": round(self.density_per_bar, 3),
            "onsets_per_bar": round(self.onsets_per_bar, 3),
            "pitch_min": self.pitch_min,
            "pitch_max": self.pitch_max,
            "pitch_range": self.pitch_range,
            "median_pitch": self.median_pitch,
            "velocity_mean": None if self.velocity_mean is None else round(self.velocity_mean, 2),
            "max_polyphony": self.max_polyphony,
            "rhythmic_entropy": round(self.rhythmic_entropy, 4),
            "syncopation": round(self.syncopation, 4),
            "silence_ratio": round(self.silence_ratio, 4),
            "repetition_ratio": round(self.repetition_ratio, 4),
            "pitch_class_histogram": {str(k): v for k, v in sorted(self.pitch_class_histogram.items())},
        }


def slice_notes(notes: list[Note], timeline_start: float, length_beats: float) -> list[Note]:
    """Return notes overlapping [timeline_start, timeline_start+length)."""
    end = timeline_start + length_beats
    # Notes are stored relative to their clips in the adapter layer; callers pass
    # absolute-timeline notes (start already offset).
    return [
        n for n in notes
        if n.start < end and (n.start + n.duration) > timeline_start
    ]


def _shannon_entropy(counts: Counter[int]) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    h = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)
    return h


def analyze_notes(notes: list[Note], span_beats: float | None = None,
                  start_beat: float = 0.0) -> SymbolicStats:
    """Compute symbolic stats over the window [start_beat, start_beat+span).

    Notes are measured in WINDOW-LOCAL time so grid/syncopation/coverage
    stay meaningful for sections that don't begin at the project origin.
    """
    notes = [
        n.model_copy(update={"start": n.start - start_beat}) for n in notes
    ]
    if not notes:
        return SymbolicStats()

    if span_beats is None:
        span_beats = max(n.start + n.duration for n in notes)

    pitches = sorted(n.pitch for n in notes)
    starts = [n.start for n in notes]

    # Grid entropy: quantize onsets to 16ths within a 4-beat bar.
    grid = Counter(int(round((s % 4.0) * 4.0)) % 16 for s in starts)
    max_h = math.log2(16)
    rhythmic_entropy = min(_shannon_entropy(grid) / max_h, 1.0) if len(grid) > 1 else 0.0

    # Syncopation proxy: onsets not landing on an integer beat.
    off_grid = sum(1 for s in starts if abs(s - round(s)) > 1e-6)
    syncopation = off_grid / len(starts)

    # Polyphony: sweep events, track max simultaneous voices.
    events: list[tuple[float, int]] = []
    for n in notes:
        events.append((n.start, 1))
        events.append((n.start + n.duration, -1))
    events.sort(key=lambda e: (e[0], e[1]))  # ends before starts at same tick
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)

    # Clamp to [0, span] and drop empties: inverted intervals (note past
    # span) silently corrupt the coverage merge.
    intervals = []
    for n in notes:
        lo = max(n.start, 0.0)
        hi = min(n.start + n.duration, span_beats)
        if hi > lo:
            intervals.append((lo, hi))
    covered = 0.0
    if intervals:
        intervals.sort()
        cur_lo, cur_hi = intervals[0]
        for lo, hi in intervals[1:]:
            if lo > cur_hi:
                covered += cur_hi - cur_lo
                cur_lo, cur_hi = lo, hi
            else:
                cur_hi = max(cur_hi, hi)
        covered += cur_hi - cur_lo
    silence_ratio = max(0.0, min(1.0, 1.0 - (covered / span_beats))) if span_beats > 0 else 0.0

    # Best-of {trigram, bigram} recurrence: trigrams avoid trivial
    # single-step repeats, bigrams catch motifs repeating across an
    # octave leap (A-C-E ... A-C-E up an octave breaks trigram runs).
    by_start = sorted(notes, key=lambda n: (n.start, n.pitch))
    intervals_seq = [b.pitch - a.pitch for a, b in zip(by_start, by_start[1:])]
    repetition = 0.0
    for ngram_len in (3, 2):
        grams = list(zip(*(intervals_seq[i:] for i in range(ngram_len))))
        if len(grams) >= 2:
            counts = Counter(grams)
            repeated = sum(c for c in counts.values() if c > 1)
            repetition = max(repetition, repeated / len(grams))

    vel = [n.velocity for n in notes]

    return SymbolicStats(
        note_count=len(notes),
        span_beats=span_beats,
        density_per_bar=len(notes) / (span_beats / 4.0) if span_beats > 0 else 0.0,
        onsets_per_bar=len(starts) / (span_beats / 4.0) if span_beats > 0 else 0.0,
        pitch_min=pitches[0],
        pitch_max=pitches[-1],
        pitch_range=pitches[-1] - pitches[0],
        median_pitch=float(pitches[len(pitches) // 2]),
        velocity_mean=sum(vel) / len(vel),
        max_polyphony=peak,
        rhythmic_entropy=rhythmic_entropy,
        syncopation=syncopation,
        silence_ratio=silence_ratio,
        repetition_ratio=repetition,
        pitch_class_histogram=dict(Counter(p % 12 for p in pitches)),
    )
