"""A small, complete example song built with the composition helpers.

``example_song()`` returns (spec, score) dicts: 8 bars at 150 BPM, a verse
and a drop over vi-IV-I-V in A minor/C major, using only built-in presets
(no plugins), so it builds on any MAGDA with the stock devices. It is the
template ``composerbench song example`` prints and what the tests build.
"""
from __future__ import annotations

from composerbench.composition.patterns import arpeggio, chord_notes, chord_root, hits, legato, note, progression, ramp
from composerbench.composition.score import Score


def example_score() -> Score:
    sc = Score.from_bars(150, [("verse", 4, "verse", 0, 0.4), ("drop", 4, "drop", 0, 0.9)],
                         title="example", key="A minor")
    chords = ["Am", "F", "C", "G"] * 2
    sc.chords_from_bars(chords)
    voicings = progression(chords, 55, 76, 4)
    for bar, (sym, v) in enumerate(zip(chords, voicings)):
        b = sc.beat(bar)
        drop = bar >= 4
        sc.add("kick", hits("x...x...x...x..." if drop else "x.........x.....", b, 36, 112))
        sc.add("snare", hits("....x.......x..r" if bar % 4 == 3 else "....x.......x...", b, 38, 100))
        sc.add("hats", hits("xxxXxxxXxxxXxxRR" if drop else "x.x.x.x.x.x.x.x.", b, 42, 80))
        root = chord_root(sym, 28)
        line = [note(b, 1.5, root, 110), note(b + 1.5, 0.5, root, 96), note(b + 2.5, 1.0, root + 12, 100),
                note(b + 3.5, 0.5, root, 100)]
        sc.add("bass", legato(line, [False, False, True, False]))
        sc.add("chords", chord_notes(v, b, 3.9, 96 if drop else 70))
        sc.add("arp", arpeggio(v, [0, 2, 1, 3, 2, 4, 3, 5], b, 0.5, 0.4, 72 if drop else 60))
    hooks = {  # one phrase per chord, chord tones on the strong beats
        "Am": [(0, .5, 76), (.5, .5, 79), (1, 1, 81), (2, .5, 79), (2.5, .5, 76), (3, 1, 72)],
        "F": [(0, .5, 77), (.5, .5, 81), (1, 1, 84), (2, .5, 81), (2.5, .5, 77), (3, 1, 79)],
        "C": [(0, .5, 76), (.5, .5, 79), (1, 1, 84), (2, .5, 79), (2.5, .5, 76), (3, 1, 74)],
        "G": [(0, .5, 74), (.5, .5, 79), (1, 1, 83), (2, .5, 79), (2.5, .5, 74), (3, 1, 71)],
    }
    for bar in range(4, 8):
        b = sc.beat(bar)
        sc.add("lead", [note(b + t, d, p, 104) for t, d, p in hooks[chords[bar]]])
    sc.add("riser", ramp([note(sc.beat(3) + i * 0.25, 0.22, 57 + i, 40) for i in range(16)], 30, 120))
    return sc.sort()


def example_song() -> tuple[dict, dict]:
    score = example_score()
    spec = {
        "title": "composerbench example",
        "tracks": [
            {"name": "kick", "preset": "kick_punch"},
            {"name": "snare", "preset": "snare_tight"},
            {"name": "hats", "preset": "hat_closed"},
            {"name": "bass", "preset": "bass_808"},
            {"name": "chords", "preset": "supersaw_poly"},
            {"name": "arp", "preset": "pluck"},
            {"name": "lead", "preset": "lead_glide"},
            {"name": "riser", "preset": "riser"},
        ],
        "render": {"stems": True, "tail_beats": 4},
    }
    return spec, score.to_dict()
