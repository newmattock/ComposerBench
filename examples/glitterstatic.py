"""GLITTERSTATIC: a hyperpop instrumental built with ComposerBench's production layer.

160 BPM, E major with a whole-step lift to F# major for the last chorus,
80 bars (~2:00). The topline is played by synths: a glide lead, a nasal
"chop" square for the hook fragments (band-passed like a phone in the intro and
outro), an octave FM sparkle in the choruses and a harmony line.

    intro  0   phone hook, filtered pads, muffled kick, static
    verse1 4   half-time trap drums, 808 with slides, bell arps
    pre1   12  four on the floor, rising pad + riser, one dead beat
    chorus1 16 supersaws, royal road Amaj7-B-G#m7-C#m7
    drop1  24  glide lead + chops, hard sidechain
    verse2 32 / pre2 40 / chorus2 44 (glitch stutter out) / bridge 52
    chorus3 60 (+2 semitones) / drop2 68 / outro 76: tape stop, call-ended beeps

Pipeline (each step is a ComposerBench tool):
  1. compose()   -> Score (notes, sections, chord track)       score checks: harmony clashes
  2. song_spec() -> MAGDA tracks from presets + overrides       check_song / build_song
  3. mix_spec()  -> automation, sidechain, cuts, tape stop      run_mix -> master.wav/.mp3

usage:
  .venv/bin/python examples/glitterstatic.py            build + mix (needs magda_cli)
  .venv/bin/python examples/glitterstatic.py --check    score + spec preflight only
  .venv/bin/python examples/glitterstatic.py --mix-only re-mix existing stems (seconds)
  options: --out DIR (default ~/Music/ComposerBench/glitterstatic), --no-plugins (no Surge XT)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from composerbench.analysis.harmony import check_score  # noqa: E402
from composerbench.composition.patterns import NOTE_NAMES, hits, legato, note  # noqa: E402
from composerbench.composition.score import Score  # noqa: E402

BPM = 160
SECTIONS = [  # name, bars, kind, key shift (semitones)
    ("intro", 4, "intro", 0), ("verse1", 8, "verse", 0), ("pre1", 4, "pre", 0),
    ("chorus1", 8, "chorus", 0), ("drop1", 8, "drop", 0), ("verse2", 8, "verse", 0),
    ("pre2", 4, "pre", 0), ("chorus2", 8, "chorus", 0), ("bridge", 8, "bridge", 0),
    ("chorus3", 8, "chorus", 2), ("drop2", 8, "drop", 2), ("outro", 4, "outro", 2),
]
ENERGY = {"intro": 0.2, "verse": 0.45, "pre": 0.6, "chorus": 0.85, "drop": 0.95, "bridge": 0.4, "outro": 0.2}

# ------------------------------------------------------------------ harmony --
# explicit voicings (MIDI, home key E major) and 808 roots
CH = {
    "C#m": ([56, 61, 64, 68], 37), "A": ([57, 61, 64, 69], 33), "E": ([56, 59, 64, 68], 28),
    "B": ([54, 59, 63, 66], 35), "F#m7": ([54, 57, 61, 64], 30), "G#m7": ([54, 59, 63, 68], 32),
    "Amaj7": ([56, 61, 64, 69], 33), "Bsus4": ([54, 59, 64, 66], 35), "C#m7": ([56, 61, 64, 71], 37),
    "C#7": ([56, 59, 65, 68], 37),
}
ROYAL = ["Amaj7", "B", "G#m7", "C#m7"]
PROG = {"intro": ROYAL, "verse": ["C#m", "A", "E", "B"] * 2, "pre": ["F#m7", "G#m7", "Amaj7", ["Bsus4", "B"]],
        "chorus": ROYAL * 2, "drop": ROYAL * 2,
        "bridge": ["Amaj7", "G#m7", "F#m7", "B", "Amaj7", "G#m7", "F#m7", "C#7"], "outro": ["Amaj7"] * 4}
SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def transpose_symbol(sym: str, k: int) -> str:
    root = sym[:2] if len(sym) > 1 and sym[1] in "#b" else sym[:1]
    pc = (NOTE_NAMES[root[0]] + (1 if root.endswith("#") else -1 if root.endswith("b") else 0) + k) % 12
    return SHARP[pc] + sym[len(root):]


class Song:
    """Section-aware note writer (bars and 16th steps -> beats)."""

    def __init__(self):
        self.sc = Score.from_bars(BPM, [(n, b, kind, key, ENERGY[kind]) for n, b, kind, key in SECTIONS],
                                  title="glitterstatic", key="E major")
        self.rng = random.Random(160)
        for s in self.sc.sections:
            bars = int((s.end - s.start) // 4)
            prog = PROG[s.kind]
            syms = []
            for b in range(bars):
                c = prog[b % len(prog)]
                syms.append([transpose_symbol(x, s.key_shift) for x in c] if isinstance(c, list)
                            else transpose_symbol(c, s.key_shift))
            self.sc.chords_from_bars(syms, start_bar=int(s.start // 4))

    def sec(self, name):
        return self.sc.section(name)

    @staticmethod
    def at(s, bar, step=0.0) -> float:
        return s.start + bar * 4 + step / 4

    def chord(self, s, bar, step=0.0):
        """(voicing, 808 root) under bar/step of section s, in that section's key."""
        prog = PROG[s.kind]
        c = prog[bar % len(prog)]
        if isinstance(c, list):
            c = c[min(len(c) - 1, int(step // (16 / len(c))))]
        v, b = CH[c]
        return [p + s.key_shift for p in v], b + s.key_shift

    def add(self, part, notes):
        self.sc.add(part, notes)


# ------------------------------------------------------------------- drums ----
def drums(g: Song, s, bar, k=None, sn=None, c=None, h=None, oh=None, kv=1.0, sv=1.0, cv=1.0, hv=1.0, ohv=0.8,
          kick_part="kick"):
    t = g.at(s, bar)
    if k:
        g.add(kick_part, hits(k, t, 36, 104 * kv))
    if sn:
        g.add("snare", hits(sn, t, 38, 100 * sv))
    if c:
        g.add("clap", hits(c, t, 39, 100 * cv))
    if h:
        g.add("hat", [{**n, "velocity": max(1, min(127, round(n["velocity"] * (0.8 + 0.4 * g.rng.random()))))}
                      for n in hits(h, t, 42, 95 * hv)])
    if oh:
        g.add("ohat", hits(oh, t, 46, 100 * ohv, length=0.35))


BASS = {
    "verseA": [(0, 10, 0, 0), (10, 6, 0, 0)],
    "verseB": [(0, 7, 0, 0), (7, 3, 12, 0), (10, 6, 0, 1)],
    "pre": [(0, 4, 0, 0), (4, 4, 0, 0), (8, 4, 0, 0), (12, 4, 0, 0)],
    "preEnd": [(0, 6, 0, 0), (6, 2, 0, 0), (8, 4, 0, 0)],
    "chorusA": [(0, 6, 0, 0), (6, 4, 0, 0), (10, 6, 0, 0)],
    "chorusB": [(0, 10, 0, 0), (10, 3, 0, 0), (13, 3, 12, 1)],
    "dropA": [(0, 10, 0, 0), (10, 6, 0, 0)],
    "dropB": [(0, 3, 0, 0), (3, 3, 12, 0), (6, 4, 0, 0), (10, 6, 0, 1)],
}


def bass(g: Song, s, bar, pat, level=0.95, part="bass"):
    notes, slides = [], []
    for st, ln, off, sl in pat:
        _, root = g.chord(s, bar, st)
        notes.append(note(g.at(s, bar, st), ln / 4, root + off, 127 * level))
        slides.append(bool(sl))
    g.add(part, notes)
    g.sc.meta.setdefault("slides", {}).setdefault(part, []).extend(
        [n["start"] for n, sl in zip(notes, slides) if sl])


def bells(g: Song, s, bar, pat, step_len, octv, level=0.07, part="bells"):
    for i, idx in enumerate(pat):
        st = i * step_len
        v, _ = g.chord(s, bar, st)
        tones = sorted(set(v + [p + 12 for p in v]))
        vel = 110 * level / 0.07 * (0.85 + 0.3 * g.rng.random())
        g.add(part, [note(g.at(s, bar, st), step_len / 4 * 0.9, tones[idx % len(tones)] + octv, vel)])


def pad(g: Song, s, bar, vel, part="pad", length=None):
    prog = PROG[s.kind][bar % len(PROG[s.kind])]
    segs = prog if isinstance(prog, list) else [prog]
    seg = 16 / len(segs)
    for i in range(len(segs)):
        v, _ = g.chord(s, bar, i * seg)
        d = (length if length is not None else seg / 4) - 0.02
        g.add(part, [note(g.at(s, bar, i * seg), d, p, vel) for p in v])


# ----------------------------------------------------------------- melodies --
# (step, length in steps, MIDI) per line, 2 bars each; topline of the original song
VERSE1 = [(0, [(0, 2, 76), (2, 2, 76), (4, 4, 73), (8, 2, 71), (10, 2, 73), (12, 2, 76), (14, 4, 78), (18, 2, 76), (20, 8, 73)]),
          (2, [(0, 2, 76), (2, 2, 76), (4, 4, 71), (8, 2, 71), (10, 2, 73), (12, 2, 76), (14, 4, 80), (18, 2, 78), (20, 8, 75)]),
          (4, [(0, 2, 76), (2, 1, 76), (3, 1, 73), (4, 2, 73), (6, 2, 76), (8, 2, 76), (10, 4, 80), (14, 2, 78), (16, 8, 76)]),
          (6, [(0, 2, 76), (2, 2, 76), (4, 2, 73), (6, 2, 71), (8, 4, 76), (12, 2, 73), (14, 2, 76), (16, 2, 78), (18, 10, 75)])]
VERSE2 = [(0, [(0, 2, 76), (2, 4, 76), (6, 2, 73), (8, 2, 71), (10, 2, 73), (12, 2, 76), (14, 4, 78), (18, 2, 76), (20, 8, 73)]),
          (2, [(0, 2, 76), (2, 4, 76), (6, 2, 71), (8, 2, 73), (10, 4, 76), (14, 2, 80), (16, 2, 78), (18, 2, 78), (20, 8, 75)]),
          (4, [(0, 2, 76), (2, 2, 76), (4, 2, 73), (6, 2, 73), (8, 2, 76), (10, 2, 76), (12, 2, 80), (14, 2, 78), (16, 8, 76)]),
          (6, [(0, 2, 76), (2, 2, 76), (4, 2, 73), (6, 2, 71), (8, 4, 76), (12, 2, 73), (14, 2, 76), (16, 2, 78), (18, 10, 75)])]
PRE = [(0, [(0, 2, 73), (2, 4, 69), (6, 2, 76), (8, 4, 73), (12, 2, 78), (14, 2, 76), (16, 2, 75), (18, 2, 78), (20, 8, 80)]),
       (2, [(0, 2, 76), (2, 2, 76), (4, 4, 80), (8, 2, 81), (10, 2, 80), (12, 2, 78), (14, 4, 83), (18, 2, 81),
            (20, 4, 83), (24, 1, 83), (25, 1, 85), (26, 1, 87), (27, 1, 88)])]
CHORUS = [(0, [(0, 2, 76), (2, 2, 78), (4, 3, 81), (7, 1, 80), (8, 2, 81), (10, 2, 80), (12, 4, 83), (16, 6, 78)]),
          (2, [(0, 2, 78), (2, 2, 80), (4, 3, 83), (7, 1, 80), (8, 4, 83), (12, 4, 80), (16, 8, 76)]),
          (4, [(0, 2, 76), (2, 2, 78), (4, 3, 81), (7, 1, 80), (8, 2, 81), (10, 2, 83), (12, 4, 85), (16, 6, 83)]),
          (6, [(0, 2, 83), (2, 2, 80), (4, 2, 78), (6, 2, 80), (8, 2, 78), (10, 2, 75), (12, 16, 73)])]
BRIDGE = [(0, [(0, 2, 73), (2, 2, 73), (4, 4, 76), (8, 4, 80), (12, 4, 78), (16, 4, 75), (20, 2, 75), (22, 8, 71)]),
          (2, [(0, 2, 73), (2, 2, 73), (4, 4, 76), (8, 4, 81), (12, 2, 80), (14, 2, 78), (16, 12, 75)]),
          (4, [(0, 4, 76), (4, 4, 80), (8, 2, 81), (10, 2, 80), (12, 4, 83), (16, 2, 80), (18, 10, 83)]),
          (6, [(0, 2, 81), (2, 2, 80), (4, 4, 78), (8, 2, 76), (10, 2, 78), (12, 2, 80), (14, 14, 80)])]
OUTRO_LINE = [(1, [(0, 2, 76), (2, 2, 78), (4, 3, 81), (7, 1, 80), (8, 2, 81), (10, 2, 80), (12, 8, 83)])]
CHOP_INTRO = [(10, 1, 80), (11, 1, 80), (12, 1, 83), (13, 1, 83), (14, 2, 85)]
CHOP_CHORUS_END = [(12, 2, 88), (14, 2, 85)]
CHOP_DROP_A = [(8, 1, 83), (9, 1, 83), (10, 2, 85), (12, 2, 87), (14, 2, 83)]
CHOP_DROP_B = [(8, 1, 80), (9, 1, 80), (10, 2, 83), (12, 4, 85)]
CHOP_DROP_END = [(8, 1, 80), (9, 1, 81), (10, 1, 83), (11, 1, 85), (12, 4, 88)]
CHOP_OUTRO = [(8, 2, 85), (10, 6, 83)]
LEAD_A = [(0, 2, 81), (2, 1, 80), (3, 1, 81), (4, 2, 83), (6, 2, 85), (8, 3, 88), (11, 1, 85), (12, 2, 83),
          (14, 2, 85), (16, 3, 87), (19, 1, 85), (20, 4, 83)]
LEAD_B = [(0, 2, 80), (2, 1, 78), (3, 1, 80), (4, 2, 83), (6, 2, 85), (8, 3, 87), (11, 1, 85), (12, 2, 83),
          (14, 2, 85), (16, 3, 88), (19, 1, 87), (20, 4, 85)]
SCALE_E = [4, 6, 8, 9, 11, 1, 3]


def phrase(g: Song, s, bar, cells, part, vel=100, shift=0, gate=1.0):
    g.add(part, [note(g.at(s, bar, st), ln / 4 * gate, m + s.key_shift + shift, vel) for st, ln, m in cells])


def lines(g: Song, s, table, part, vel=100, shift=0):
    for bar, cells in table:
        phrase(g, s, bar, cells, part, vel, shift)


def diatonic(m: int, degrees: int) -> int:
    pc = m % 12
    if pc not in SCALE_E:
        return m + (4 if degrees > 0 else -3)
    j = SCALE_E.index(pc) + degrees
    target = SCALE_E[j % 7]
    d = target - pc
    if degrees > 0:
        while d <= 0:
            d += 12
    else:
        while d >= 0:
            d -= 12
    return m + d


def harmony(g: Song, s, table, degrees, from_bar=0, part="harm", vel=84):
    """Diatonic harmony a third away, skipping notes that would rub against the chord."""
    from composerbench.composition.patterns import chord_pitch_classes
    for bar, cells in table:
        if bar < from_bar:
            continue
        for st, ln, m in cells:
            t = g.at(s, bar, st)
            chords = [g.sc.chord_at(t), g.sc.chord_at(t + min(ln, 8) / 8)]

            def ok(h):
                pcs = [chord_pitch_classes(c.symbol) for c in chords if c]
                return all(h % 12 in p or not any((h - x) % 12 == 1 for x in p) for p in pcs)
            for cand in (diatonic(m, degrees) + s.key_shift, diatonic(m, -degrees) + s.key_shift):
                if ok(cand):
                    g.add(part, [note(t, ln / 4, cand, vel)])
                    break


def riser_steps(g: Song, start, beats, base=61, v0=30, v1=127, part="riser"):
    out, t = [], 0.0
    while t < beats - 0.01:
        frac = t / beats
        step = 0.5 if frac < 0.5 else 0.25
        out.append(note(start + t, step * 0.9, base + int(frac * 24), v0 + (v1 - v0) * frac))
        t += step
    g.add(part, out)


def downlift(g: Song, start, beats, top=85, part="riser"):
    out, t = [], 0.0
    while t < beats - 0.01:
        frac = t / beats
        out.append(note(start + t, 0.22, top - int(frac * 24), 110 - 80 * frac))
        t += 0.25
    g.add(part, out)


def static(g: Song, start, end, level):
    """A static bed note; level 0..0.12 as in the original mix."""
    if end > start and level > 0:
        g.add("static", [note(start, end - start, 60, min(127, max(1, 127 * level / 0.12)))])


# ------------------------------------------------------------------ compose ----
def compose() -> Score:
    g = Song()
    S = g.sec

    # INTRO: phone hook, filtered pads, muffled kick (mix stage), static
    s = S("intro")
    for b in range(4):
        pad(g, s, b, 40 + b * 20)
        bells(g, s, b, [0, 2, 1, 3, 2, 4, 3, 5], 2, 12, 0.06)
    lines(g, s, [(0, CHORUS[0][1]), (2, CHORUS[1][1])], "chops", 96)
    phrase(g, s, 3, CHOP_INTRO, "chops", 104)
    drums(g, s, 2, k="x...x...x...x...", kv=0.7)
    drums(g, s, 3, k="x...x...x...x...", sn="........x.x.xxrr", kv=0.85, sv=0.45)
    riser_steps(g, g.at(s, 2), 8, base=57, v0=24, v1=96)
    static(g, 0, g.at(s, 3), 0.055)
    static(g, g.at(s, 3), s.end - 0.05, 0.03)

    def verse(name, table, v2):
        s = S(name)
        for b in range(8):
            pad(g, s, b, 62)
            if v2:
                bells(g, s, b, [0, 2, 4, 2, 1, 3, 5, 3, 2, 4, 6, 4, 3, 5, 7, 5], 1, 24, 0.03)
            else:
                bells(g, s, b, [0, 2, 1, 3, 2, 4, 3, 5], 2, 24, 0.042)
            bass(g, s, b, BASS["verseB" if b % 2 else "verseA"], 0.72)
            if v2:
                h = ["x.xxx.x.x.xxx.x.", "x.x.x.x.x.x.3---", "x.xxx.x.x.x.x.rr", "x.x.x.x.3---xxxx"][b % 4]
            else:
                h = ("x.x.x.x.xxxxrrrr" if b == 7 else "x.x.x.x.x.x.3---" if b == 3 else
                     ("x.x.x.x.x.x.x.rr" if b % 2 else "x.x.x.x.x.x.x.x."))
            k = ("x......x..x...x." if v2 and b % 4 == 3 else "x......x..x.....") if b % 2 else "x.........x....."
            drums(g, s, b, k=k, c="........x.......", h=h, kv=0.9, cv=0.8, hv=0.7)
        lines(g, s, table, "topline", 96)
        g.add("crash", [note(s.start, 2, 49, 70)])
        g.add("impact", [note(s.start, 2, 36, 60)])
        if v2:
            downlift(g, s.start, 4)

    def pre(name):
        s = S(name)
        for b in range(4):
            pad(g, s, b, [70, 82, 94, 106][b])
            bells(g, s, b, [0, 1, 2, 3, 4, 3, 2, 1, 2, 3, 4, 5, 6, 5, 4, 3], 1, 12, 0.028 + 0.007 * b)
            bass(g, s, b, BASS["pre" if b < 3 else "preEnd"])
        drums(g, s, 0, k="x...x...x...x...", c="........x.......", h="xxxxxxxxxxxxxxxx", hv=0.55)
        drums(g, s, 1, k="x...x...x...x...", c="........x.......", h="xxxxxxxxxxxxxxxx", hv=0.68)
        drums(g, s, 2, k="x...x...x...x...", c="....x.......x...", h="xxxxxxxxxxxxxxxx", hv=0.8)
        drums(g, s, 3, k="x...x...x.......", sn="x.x.x.x.xrrR....", h="xxxxxxxxxxxx....", hv=0.9, sv=0.8)
        lines(g, s, PRE, "topline", 104)
        riser_steps(g, s.start, 15, base=61)
        g.add("swell", [note(g.at(s, 3, 8), 2.0, 60, 110)])
        static(g, g.at(s, 3, 12), s.end, 0.12)

    def chorus(name, nxt):
        s = S(name)
        to_bridge = nxt == "bridge"
        for b in range(8):
            last = b == 7
            if not (last and to_bridge):
                pad(g, s, b, 104, part="saws")
            bells(g, s, b, [4, 6, 5, 7, 4, 6, 5, 7, 3, 5, 4, 6, 3, 5, 4, 6], 1, 24, 0.028)
            bass(g, s, b, BASS["chorusB" if b % 2 else "chorusA"])
            h = "XxxxXxxxXxxx3---" if b % 4 == 3 else "XxxxXxxxXxxxXxxx"
            if last and to_bridge:
                drums(g, s, b, k="x.....x.........", sn="........rrrrRRRR", c="........x.......",
                      h="XxxxXxxx........", hv=0.75, sv=0.7)
            else:
                drums(g, s, b, k="x.........x..x.." if b % 2 else "x.....x...x.....", sn="........x.......",
                      c="........x.......", h=h, oh="..............x." if b % 2 else None, hv=0.75)
        lines(g, s, CHORUS, "topline", 112)
        lines(g, s, CHORUS, "sparkle", 90, shift=12)
        if name == "chorus3":
            harmony(g, s, CHORUS, -2)
        g.add("crash", [note(s.start, 2, 49, 120), note(g.at(s, 4), 2, 49, 75)])
        g.add("impact", [note(s.start, 2, 36, 127 if name == "chorus3" else 110)])
        if nxt == "drop":
            phrase(g, s, 7, CHOP_CHORUS_END, "chops", 112)
            g.add("swell", [note(g.at(s, 7, 4), 2.75, 60, 105)])
            riser_steps(g, g.at(s, 6), 7, base=64, v0=30, v1=100)
            static(g, g.at(s, 7, 12), s.end, 0.1)
        else:
            v, _ = g.chord(s, 7)
            g.add("saws", [note(g.at(s, 7), 1.98, p, 104) for p in v])
            for k in range(16):
                g.add("stab", [note(g.at(s, 7, 8) + k * 0.125, 0.07, p, 112 - k * 4) for p in v])
            downlift(g, s.end, 6)

    def drop(name, final):
        s = S(name)
        for b in range(8):
            pad(g, s, b, 96, part="saws")
            bass(g, s, b, BASS["dropB" if b % 2 else "dropA"])
            drums(g, s, b, k="x.........x..x.." if b % 2 else "x.........x.....",
                  sn="....x...xxx.rrrr" if b == 7 else ("....x..o....x..o" if b % 2 else "....x.......x..."),
                  c="....x.......x...", h="x.x.x.x.x.x.x.x.", oh="..x...x...x...x.", hv=0.8, cv=0.55, ohv=0.5)
        for b in (0, 4):
            phrase(g, s, b, LEAD_A, "lead", 108)
            if final:
                phrase(g, s, b, LEAD_A, "lead_lo", 96, shift=-12)
        for b in (2, 6):
            phrase(g, s, b, LEAD_B, "lead", 108)
            if final:
                phrase(g, s, b, LEAD_B, "lead_lo", 96, shift=-12)
        for b, chop in ((1, CHOP_DROP_A), (3, CHOP_DROP_B), (5, CHOP_DROP_A), (7, CHOP_DROP_END)):
            phrase(g, s, b, chop, "chops", 110)
        g.add("crash", [note(s.start, 2, 49, 127), note(g.at(s, 4), 2, 49, 80)])
        g.add("impact", [note(s.start, 2, 36, 120)])

    def bridge():
        s = S("bridge")
        for b in range(8):
            pad(g, s, b, 64)
            if b < 4:
                bells(g, s, b, [0, 2, 4, 3], 4, 12, 0.055, part="bells_soft")
            else:
                bells(g, s, b, [0, 2, 1, 3, 2, 4, 3, 5], 2, 12, 0.045, part="bells_soft")
            bass(g, s, b, [(0, 16, 0, 0)], 0.75, part="sub")
        for b in range(4, 7):
            drums(g, s, b, k="x.......x.......", c="........x.......", h="..x...x...x...x.", kv=0.8, cv=0.6, hv=0.5)
        drums(g, s, 7, k="x...x...x...x...", sn="x...x.x.xxxxrrrr", h="x.x.x.x.x.x.x.x.", sv=0.75, hv=0.6)
        lines(g, s, BRIDGE, "topline", 92)
        harmony(g, s, BRIDGE, 2, from_bar=4)
        static(g, s.start, g.at(s, 2), 0.04)
        riser_steps(g, g.at(s, 4), 15, base=61, v1=127)
        g.add("swell", [note(g.at(s, 7, 4), 2.75, 60, 120)])
        static(g, g.at(s, 7, 12), s.end, 0.12)

    def outro():
        s = S("outro")
        pad(g, s, 0, 76, length=g.at(s, 2) - s.start + 3.3)          # rings into the tape stop
        bells(g, s, 0, [0, 2, 1, 3, 2, 4, 3, 5], 2, 12, 0.045)
        bells(g, s, 1, [0, 2, 1, 3, 2, 4, 3, 5], 2, 12, 0.04)
        bass(g, s, 0, [(0, 16, 0, 0)], 0.6, part="sub")
        phrase(g, s, 0, CHOP_OUTRO, "chops", 104)
        lines(g, s, OUTRO_LINE, "chops", 96)
        g.add("crash", [note(s.start, 2, 49, 90)])
        g.add("impact", [note(s.start, 2, 36, 85)])
        static(g, s.start, g.at(s, 1), 0.02)
        static(g, g.at(s, 1), g.at(s, 2), 0.045)
        static(g, g.at(s, 2), g.at(s, 3, 8), 0.08)
        g.add("beep", [note(g.at(s, 3, 1), 0.45, 75, 110), note(g.at(s, 3, 4), 0.45, 75, 110)])

    verse("verse1", VERSE1, False)
    pre("pre1")
    chorus("chorus1", "drop")
    drop("drop1", False)
    verse("verse2", VERSE2, True)
    pre("pre2")
    chorus("chorus2", "bridge")
    bridge()
    chorus("chorus3", "drop")
    drop("drop2", True)
    outro()

    # 808 slides: overlap a flagged note into the next so the Legato synth glides
    slides = set(g.sc.meta.pop("slides", {}).get("bass", []))
    for part in ("bass", "sub"):
        ns = sorted(g.sc.parts.get(part, []), key=lambda n: n["start"])
        g.sc.parts[part] = legato(ns, [n["start"] in slides for n in ns] if part == "bass" else None)
    for part in ("lead", "lead_lo", "topline"):             # mono lines: clean retriggers
        g.sc.parts[part] = legato(sorted(g.sc.parts.get(part, []), key=lambda n: n["start"]))
    return g.sc.sort()


# -------------------------------------------------------------- song + mix ----
def song_spec(score_path: str, plugins: bool = True) -> dict:
    # Fader values come from a balance pass on real MAGDA renders (per-track
    # loudness where each part leads, set relative to the kick); Surge XT's
    # saws were not part of that pass, so they keep the preset's level.
    saws = {"name": "saws", "preset": "supersaw_surge"} if plugins else \
        {"name": "saws", "preset": "supersaw_poly", "volume": 1.04}
    return {
        "title": "glitterstatic",
        "score": score_path,
        "tracks": [
            {"name": "kick", "preset": "kick_punch", "volume": 0.228},
            {"name": "snare", "preset": "snare_tight", "volume": 0.294},
            {"name": "clap", "preset": "clap_wide", "volume": 0.36},
            {"name": "hat", "preset": "hat_closed", "volume": 0.329},
            {"name": "ohat", "preset": "hat_open", "volume": 0.305},
            {"name": "bass", "preset": "bass_808", "volume": 0.147},
            {"name": "sub", "preset": "sub_sine", "volume": 0.156},
            {"name": "pad", "preset": "pad_sweep", "volume": 0.343},
            saws,
            {"name": "stab", "preset": "supersaw_poly", "volume": 1.05,
             "params": {"Amp Decay": 80.0, "Amp Sustain": 0.6, "Amp Release": 30.0}},
            {"name": "bells", "preset": "bell_fm", "params": {"Op2 Ratio": 4.0, "Amp Decay": 260.0}, "volume": 0.617},
            {"name": "bells_soft", "preset": "bell", "params": {"decay": 1400.0}, "volume": 1.32},
            {"name": "lead", "preset": "lead_glide", "volume": 0.414},
            {"name": "lead_lo", "preset": "lead_glide", "params": {"Cutoff": 2600.0}, "volume": 0.219},
            {"name": "topline", "preset": "lead_glide", "stem": "topline", "volume": 0.389,
             "params": {"Cutoff": 3400.0, "Glide": 30.0, "Amp Attack": 8.0}},
            {"name": "sparkle", "preset": "lead_fm_crushed", "stem": "topline", "volume": 0.281},
            {"name": "harm", "preset": "lead_fm_crushed", "stem": "topline", "volume": 0.44,
             "params": {"M21": 0.8}},
            {"name": "chops", "preset": "chop_square", "stem": "chops", "volume": 0.516},
            {"name": "riser", "preset": "riser", "volume": 0.643},
            {"name": "swell", "preset": "noise_swell", "params": {"Amp Attack": 600.0}, "volume": 0.236},
            {"name": "static", "preset": "static_crush", "volume": 0.055},
            {"name": "crash", "preset": "crash_noise", "volume": 0.314},
            {"name": "impact", "preset": "impact_boom", "volume": 0.309},
            {"name": "beep", "preset": "beep", "volume": 0.216},
        ],
        "render": {"stems": True, "tail_beats": 4, "bit_depth": 24},
    }


def mix_spec() -> dict:
    # filters on the music stems: dark intro opening into the verse, a dark bridge that sweeps open
    # into the last chorus, a closing outro; high-pass "lifts" in the dead beat before each drop
    music = {"lowpass_hz": [[0, 500], ["intro.end-0.25", 4500], ["intro.end", 20000, "jump"],
                            ["bridge", 2000, "jump"], ["bridge+16", 2000], ["bridge.end-0.1", 18000],
                            ["bridge.end", 20000, "jump"], ["outro", 20000], ["outro+8", 1100]],
             "highpass_hz": [[0, 20], ["pre1.end-1", 20, "jump"], ["pre1.end-0.05", 650], ["pre1.end", 20, "jump"],
                             ["pre2.end-1", 20, "jump"], ["pre2.end-0.05", 650], ["pre2.end", 20, "jump"],
                             ["bridge.end-1", 20, "jump"], ["bridge.end-0.05", 800], ["bridge.end", 20, "jump"]]}
    cuts = [{"start": f"{s}.end-1", "end": f"{s}.end", "keep": ["fx"]}
            for s in ("pre1", "chorus1", "pre2", "bridge", "chorus3")]
    every_stem_but_fx = ["drums", "bass", "synths", "lead", "topline", "chops"]
    return {
        "score": "score.json", "stems_dir": "stems",
        "stems": {
            # pre-choruses sit 1.5 dB under the drums of the chorus they lead into
            "drums": {"automation": {"lowpass_hz": [[0, 380], ["intro+12", 650, "jump"], ["intro.end-0.05", 900],
                                                    ["intro.end", 20000, "jump"]],
                                     "gain_db": [[0, 0], ["pre1", -1.5, "jump"], ["pre1.end", 0, "jump"],
                                                 ["pre2", -1.5, "jump"], ["pre2.end", 0, "jump"]]}},
            "bass": {"mono_below_hz": 150},
            "synths": {"automation": music, "eq": [{"type": "highpass", "freq": 120}], "width": 1.3},
            "lead": {"automation": music},
            "topline": {"eq": [{"type": "highpass", "freq": 180}, {"type": "bell", "freq": 2800, "q": 1.0,
                                                                   "gain_db": 2.0}]},
            # the phone-filtered hook reads louder than its level: keep the intro and outro calls back
            "chops": {"eq": [{"type": "highpass", "freq": 250}],
                      "automation": {"gain_db": [[0, -6], ["intro.end", 0, "jump"], ["outro", -2, "jump"]]}},
            "fx": {"gain_db": -1.0},
        },
        "sidechain": [{"trigger": {"part": "kick"}, "targets": ["synths", "lead"], "depth_db": 5,
                       "section_depth_db": {"intro": 2.5, "chorus1": 7, "chorus2": 7, "chorus3": 7,
                                            "drop1": 8, "drop2": 8, "bridge": 4},
                       "release_ms": 130}],
        "cuts": cuts,
        "edits": [{"type": "telephone", "start": 0, "end": "intro.end", "stems": ["chops"], "fade": 0.5},
                  {"type": "telephone", "start": "outro+4", "end": "outro.end+8", "stems": ["chops"], "fade": 0.5},
                  {"type": "reverse_swell", "start": "drop1-2", "end": "drop1", "source_start": "drop1",
                   "stems": ["synths"], "gain_db": -9},
                  {"type": "reverse_swell", "start": "drop2-2", "end": "drop2", "source_start": "drop2",
                   "stems": ["synths"], "gain_db": -9},
                  {"type": "tape_stop", "start": "outro+8", "length": 3.33, "curve": 1.4,
                   "stems": every_stem_but_fx, "silence_after": True}],
        "bus": {"eq": [{"type": "highpass", "freq": 25, "order": 4}],
                "glue": {"threshold_db": -14, "ratio": 2, "attack_ms": 10, "release_ms": 150},
                "mono_below_hz": 120},
        "master": {"target_lufs": -10, "ceiling_dbtp": -1.0, "fade_out": 1, "end": "outro.end+4"},
        "export": {"basename": "glitterstatic_master", "mp3": True, "spectrogram": True},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path.home() / "Music" / "ComposerBench" / "glitterstatic"))
    ap.add_argument("--check", action="store_true", help="score + spec preflight only")
    ap.add_argument("--mix-only", action="store_true", help="re-run the mix on existing stems")
    ap.add_argument("--no-plugins", action="store_true", help="use the built-in supersaw instead of Surge XT")
    ap.add_argument("--engine", help="magda_cli path (default: discovered)")
    ns = ap.parse_args(argv)
    out = Path(ns.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    from composerbench.mixing.mixdown import run_mix
    from composerbench.song.build import build_song, check_song
    (out / "mix.json").write_text(json.dumps(mix_spec(), indent=1))
    if not ns.mix_only:
        score = compose()
        checks = check_score(score, ranges={"bass": (24, 55), "sub": (24, 50), "lead": (64, 100)},
                             mono=["bass", "lead", "topline"])
        print("score:", checks["harmony"]["summary"], "|", checks["ranges"]["summary"],
              "|", checks["sustain"]["summary"],
              "| problems:", checks["validation"][:3] or "none", flush=True)
        score_path = out / "composition.json"
        score.save(score_path)
        spec = song_spec(str(score_path), plugins=not ns.no_plugins)
        (out / "song.json").write_text(json.dumps(spec, indent=1))
        pre = check_song(spec, out)
        print("preflight:", "ok" if pre["ok"] else "FAILED", *pre["info"], flush=True)
        for line in pre["errors"] + pre["warnings"]:
            print("  -", line)
        if ns.check or not pre["ok"]:
            return 0 if pre["ok"] else 1
        report = asyncio.run(build_song(spec, out_dir=out, cli_path=ns.engine))
        print("build problems:", report["problems"][:12] or "none")
        print("read-back mismatches:", len(report["readback_mismatch"]))
    mixed = run_mix(out / "mix.json")
    m = mixed["master"]
    print(f"master: {m['integrated_lufs']} LUFS, true peak {m['true_peak_dbtp']} dBTP, LRA {m['loudness_range_lu']} LU")
    print("sections (LUFS):", ", ".join(f"{n} {v}" for n, v in mixed["sections"]["contour"]))
    print("mix warnings:", "; ".join(mixed["warnings"]) or "none")
    for k, v in mixed["files"].items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
