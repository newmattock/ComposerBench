"""Symbolic checks an agent can run before rendering anything.

``clash_report`` finds "avoid notes": melodic notes a half step *above* a
chord tone of the chord under them (a 4th over a major 3rd, a b9 over the
root, the 4th of a dominant...) where they are most audible: on strong beats
or when the note is long. Notes a half step *below* a chord tone (major 7ths,
9ths under a minor 3rd, #11s) are ordinary tensions and are not flagged. ``range_report`` flags
notes outside a part's comfortable register and overlapping notes in parts
meant to be monophonic. Neither is a taste judgment: passing tones and
deliberate suspensions are fine when short and off the beat.
"""
from __future__ import annotations

from composerbench.composition.patterns import chord_pitch_classes
from composerbench.composition.score import Score

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
DEFAULT_PERCUSSION = ("kick", "snare", "clap", "hat", "ohat", "crash", "impact", "zap", "noise", "static",
                      "riser", "swell", "perc", "tom", "beep", "drums")


def note_name(m: int) -> str:
    return f"{NAMES[m % 12]}{m // 12 - 1}"


def _is_percussion(part: str, percussion) -> bool:
    low = part.lower()
    return any(low == p or low.startswith(p) for p in percussion)


def clash_report(score: Score, parts: list[str] | None = None, min_beats: float = 0.5,
                 strong_every: float = 1.0, percussion=DEFAULT_PERCUSSION) -> dict:
    """Semitone clashes between melodic notes and the chord track.

    A note is checked at its onset and at the middle of its first beat. It
    clashes when its pitch class is not a chord tone and lies a semitone above
    one. Clashes count when the note is at least ``min_beats`` long or starts
    on a multiple of ``strong_every`` beats.
    """
    if not score.chords:
        return {"checked": 0, "clashes": [], "note": "score has no chords; nothing to check"}
    names = parts or [p for p in score.parts if not _is_percussion(p, percussion)]
    clashes, checked = [], 0
    for part in names:
        for n in score.parts.get(part, []):
            start, dur = n["start"], n["duration"]
            strong = abs(start / strong_every - round(start / strong_every)) < 1e-6
            if dur < min_beats and not strong:
                continue
            checked += 1
            pc = n["pitch"] % 12
            for probe in (start, start + min(dur, 1.0) / 2):
                chord = score.chord_at(probe)
                if chord is None:
                    continue
                tones = chord_pitch_classes(chord.symbol)
                if pc in tones:
                    continue
                rub = sorted(t for t in tones if (pc - t) % 12 == 1)
                if rub:
                    clashes.append({"part": part, "beat": round(start, 4), "bar": int(start // score.beats_per_bar),
                                    "note": note_name(n["pitch"]), "duration": dur, "chord": chord.symbol,
                                    "against": [NAMES[t] for t in rub], "strong_beat": strong})
                    break
    return {"checked": checked, "clashes": clashes,
            "summary": f"{len(clashes)} clash(es) in {checked} checked notes"}


def range_report(score: Score, ranges: dict[str, tuple[int, int]] | None = None,
                 mono: list[str] | None = None) -> dict:
    """Out-of-range notes per part, and overlaps in parts listed as ``mono``."""
    issues = []
    for part, (lo, hi) in (ranges or {}).items():
        for n in score.parts.get(part, []):
            if not lo <= n["pitch"] <= hi:
                issues.append({"part": part, "beat": n["start"], "note": note_name(n["pitch"]),
                               "problem": f"outside {note_name(lo)}-{note_name(hi)}"})
    for part in mono or []:
        ns = sorted(score.parts.get(part, []), key=lambda n: n["start"])
        for a, b in zip(ns, ns[1:]):
            overlap = a["start"] + a["duration"] - b["start"]
            if overlap > 0.1:          # small overlaps are intentional legato slides
                issues.append({"part": part, "beat": b["start"], "note": note_name(b["pitch"]),
                               "problem": f"overlaps previous note by {overlap:.2f} beats (mono part)"})
    return {"issues": issues, "summary": f"{len(issues)} range/overlap issue(s)"}


def density_report(score: Score) -> dict:
    """Notes per bar for each part in each section: a quick energy-arc check."""
    out = {}
    for s in score.sections:
        bars = max(1e-9, (s.end - s.start) / score.beats_per_bar)
        row = {}
        for part, ns in score.parts.items():
            count = sum(1 for n in ns if s.start <= n["start"] < s.end)
            if count:
                row[part] = round(count / bars, 2)
        out[s.name] = row
    return out


def sustain_report(score: Score, max_bars: float = 8.0, spill_bars: float = 1.0) -> dict:
    """Notes that ring on where they almost certainly should not.

    Flags a note that runs more than ``spill_bars`` into a later section in
    which its part starts nothing before the note ends (a slide or legato at
    the end of a section stretched to the next entry), and any note longer
    than ``max_bars`` bars.
    """
    bpb = score.beats_per_bar
    issues = []
    for part, ns in score.parts.items():
        starts = sorted(n["start"] for n in ns)
        for n in ns:
            start, end = n["start"], n["start"] + n["duration"]
            home = score.section_at(start)
            problem = None
            if n["duration"] > max_bars * bpb:
                problem = f"lasts {n['duration'] / bpb:.1f} bars"
            for sec in score.sections:
                if home is not None and sec.start < home.end:
                    continue
                spill = min(end, sec.end) - sec.start
                if spill <= spill_bars * bpb:
                    break
                if not any(sec.start <= t < min(end, sec.end) for t in starts):
                    problem = (f"sustains {spill / bpb:.1f} bars into '{sec.name}', where {part} plays "
                               "nothing else (a slide or legato reaching a far-off note?)")
                break
            if problem:
                issues.append({"part": part, "beat": round(start, 4), "note": note_name(n["pitch"]),
                               "duration": round(n["duration"], 3), "problem": problem})
    return {"issues": issues, "summary": f"{len(issues)} runaway note(s)"}


def check_score(score: Score, ranges=None, mono=None, clip_length=None) -> dict:
    """Everything above plus Score.validate(), in one report."""
    return {"validation": score.validate(clip_length), "harmony": clash_report(score),
            "ranges": range_report(score, ranges, mono), "sustain": sustain_report(score),
            "density": density_report(score), "parts": score.stats()}
