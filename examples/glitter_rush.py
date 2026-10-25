"""GLITTER RUSH — an energetic hyperpop track composed with ComposerBench over MAGDA.

160 BPM, 4/4, 80 bars (~2:00). A major, with a whole-step truck-driver key
change into B major for the last drop.

  bars  0-7   INTRO   velocity-swept pad (filter opens via Vel->Filter), bells
  bars  8-23  VERSE   half-time trap drums, 808 with slides, pluck arp, verse lead
  bars 24-31  PRE     four-on-floor, snare roll, riser + noise swell, 1-beat gap
  bars 32-47  DROP 1  Surge XT supersaws, syncopated 808, hook lead, zaps
  bars 48-55  BREAK   half-time, IV-V-iii-vi, bell/FM hook echo
  bars 56-59  BUILD   D-E-F#-F# lift, roll, riser, gap
  bars 60-75  DROP 2  everything +2 semitones, octave-up bitcrushed FM double
  bars 76-79  OUTRO   final chord, 1/32 stutter tail, 808 dive, bell ping

Design constraints learned from this repo (examples/*.py, docs/):
- ONE clip per track starting at beat 0 (MAGDA arranges same-track clips
  sequentially), notes are clip-relative == absolute here.
- No automation/pan/sends in the CLI: sweeps use velocity->filter, panning
  uses magda_utility, and sidechain pumping is applied later from the score.
- External-plugin parameters are normalised 0..1 with no labels; the Surge XT
  values below were verified by probe renders (filter type 0.03 = LP 12 dB).

Outputs (~/Music/ComposerBench/glitter_rush/):
  mix_raw.wav, stems/*.wav    24-bit renders straight out of MAGDA
  score.json                  every note per track (used for mastering)
  session.json                devices, parameter read-back check, render stats
  project/                    the MAGDA project (.mgd)
"""
import asyncio
import json
import pathlib
import random
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from composerbench.adapter.base import RenderRequest  # noqa: E402
from composerbench.adapter.magda_serve import MagdaServeAdapter  # noqa: E402
from composerbench.analysis.audio import analyze_wav  # noqa: E402
from composerbench.engine import discover_cli  # noqa: E402

CLI = discover_cli() or pathlib.Path("magda_cli")
OUT = pathlib.Path.home() / "Music" / "ComposerBench" / "glitter_rush"
WORK = OUT / "project"

BPM = 160.0
BARS = 80
CLIP_BEATS = 328.0          # 82 bars: room for tails inside the clip
RENDER_BEATS = 324.0        # 81 bars rendered (last bar = reverb tail)
T0 = time.time()
rng = random.Random(160)


def log(*a):
    print(f"[glitter {time.time() - T0:6.1f}s]", *a, flush=True)


# =============================================================== harmony ====
SECTIONS = [("intro", 0, 8), ("verse", 8, 24), ("pre", 24, 32), ("drop1", 32, 48),
            ("break", 48, 56), ("build", 56, 60), ("drop2", 60, 76), ("outro", 76, 80)]


def section(bar):
    for name, a, b in SECTIONS:
        if a <= bar < b:
            return name
    return "tail"


VOICING = {  # 5-note voicings in A-major register (shifted for the key change)
    "F#m": [54, 61, 64, 69, 73], "D": [50, 57, 61, 66, 69], "A": [57, 61, 64, 69, 71],
    "E": [52, 59, 64, 68, 71], "C#m": [49, 56, 61, 64, 68], "F#": [54, 61, 66, 70, 73],
}
ROOT808 = {"F#m": 30, "D": 38, "A": 33, "E": 28, "C#m": 37, "F#": 30}
LOOP = ["F#m", "D", "A", "E"]


def chord(bar):
    """(name, semitone shift) for a bar."""
    s = section(bar)
    if s == "break":
        return ["D", "E", "C#m", "F#m"][bar % 4], 0
    if s == "build":
        return ["D", "E", "F#", "F#"][bar - 56], 0
    if s == "drop2":
        return LOOP[bar % 4], 2
    if s in ("outro", "tail"):
        return "A", 2                      # B major home chord
    return LOOP[bar % 4], 0


def voicing(bar):
    name, sh = chord(bar)
    return [p + sh for p in VOICING[name]]


def root(bar):
    name, sh = chord(bar)
    return ROOT808[name] + sh


def N(start, dur, pitch, vel):
    return {"start": round(float(start), 4), "dur": round(float(dur), 4),
            "pitch": int(pitch), "vel": int(max(1, min(127, round(vel))))}


# ================================================================= drums ====
def drums():
    P = {k: [] for k in ("kick", "snare", "clap", "hat", "ohat", "zap")}

    def hat_roll(b0, length, step, v0, v1):
        n = int(round(length / step))
        for i in range(n):
            P["hat"].append(N(b0 + i * step, step * 0.8, 42, v0 + (v1 - v0) * i / max(1, n - 1)))

    for bar in range(BARS):
        b = bar * 4.0
        s = section(bar)
        if s == "intro":
            if bar >= 4:                                   # hats fade in
                for i in range(8):
                    P["hat"].append(N(b + i * 0.5, 0.2, 42, 34 + (bar - 4) * 8 + (6 if i % 2 == 0 else 0)))
            if bar == 7:
                for i in range(8):                         # snare roll into verse
                    P["snare"].append(N(b + 2 + i * 0.25, 0.2, 38, 50 + i * 8))
        elif s == "verse":                                 # half-time trap
            kicks = [0.0, 2.5] if bar % 2 == 0 else [0.0, 1.75, 2.5]
            if bar % 4 == 3:
                kicks = [0.0, 0.75, 2.5, 3.25]
            for k in kicks:
                P["kick"].append(N(b + k, 0.3, 36, 112 if k == 0 else 100))
            P["clap"].append(N(b + 2.0, 0.3, 39, 108))
            P["snare"].append(N(b + 2.0, 0.25, 38, 84))
            if bar % 2 == 1:
                P["hat"].append(N(b + 3.75, 0.1, 42, 60))
            if bar % 4 == 3:                               # trap roll: 1/16 triplets
                for i in range(6):
                    P["hat"].append(N(b + i * 0.5, 0.2, 42, 72 if i % 2 == 0 else 58))
                hat_roll(b + 3.0, 1.0, 1 / 6, 52, 96)
            elif bar % 4 == 1:
                for i in range(6):
                    P["hat"].append(N(b + i * 0.5, 0.2, 42, 72 if i % 2 == 0 else 58))
                hat_roll(b + 3.0, 1.0, 0.125, 48, 92)
            else:
                for i in range(8):
                    P["hat"].append(N(b + i * 0.5, 0.2, 42, 74 if i % 2 == 0 else 56))
            if bar >= 16 and bar % 2 == 0:
                P["ohat"].append(N(b + 3.5, 0.4, 46, 64))
        elif s in ("pre", "build"):
            build_bar = bar - (24 if s == "pre" else 56)
            last = 7 if s == "pre" else 3
            for k in range(4):
                if build_bar == last and k == 3:
                    break                                  # the 1-beat gap before the drop
                P["kick"].append(N(b + k, 0.3, 36, 108))
            if s == "pre" and build_bar < 4:
                P["clap"].append(N(b + 1, 0.3, 39, 100))
                P["clap"].append(N(b + 3, 0.3, 39, 104))
                for i in range(8):
                    P["hat"].append(N(b + i * 0.5, 0.15, 42, 70 if i % 2 else 58))
            else:
                # accelerating snare roll: 1/4 -> 1/8 -> 1/16 -> 1/32
                prog = build_bar - (4 if s == "pre" else 0)
                step = [0.5, 0.5, 0.25, 0.125][min(prog, 3)] if s == "pre" else [0.5, 0.25, 0.125, 0.125][prog]
                if s == "pre" and prog == 0:
                    step = 1.0
                n = int(4 / step)
                for i in range(n):
                    t = i * step
                    if (build_bar == last) and t >= 3.0:
                        break
                    v = 60 + 60 * ((prog * 4 + t) / 16.0)
                    P["snare"].append(N(b + t, min(step * 0.8, 0.2), 38, v))
                for i in range(8):
                    if build_bar == last and i >= 6:
                        break
                    P["hat"].append(N(b + i * 0.5, 0.15, 42, 66))
        elif s in ("drop1", "drop2"):
            phrase_bar = (bar - (32 if s == "drop1" else 60)) % 4
            kicks = [0.0, 1.5, 2.0] if phrase_bar % 2 == 0 else [0.0, 1.5, 2.0, 3.25]
            if phrase_bar == 3:
                kicks = [0.0, 0.75, 1.5, 2.0, 2.75]
            for k in kicks:
                P["kick"].append(N(b + k, 0.3, 36, 118 if k in (0.0, 2.0) else 104))
            for k in (1.0, 3.0):
                P["clap"].append(N(b + k, 0.3, 39, 112))
                P["snare"].append(N(b + k, 0.25, 38, 96))
            for i in range(16):                            # 16th hats with accents
                if phrase_bar == 3 and i >= 12:
                    break
                acc = 80 if i % 4 == 2 else (66 if i % 2 == 0 else 50)
                P["hat"].append(N(b + i * 0.25, 0.1, 42, acc + rng.uniform(-4, 4)))
            if bar in (32, 60):
                P["ohat"].append(N(b, 1.5, 46, 112))       # crash-ish on the drop
            if phrase_bar == 3:
                hat_roll(b + 3.0, 1.0, 1 / 8, 60, 110)     # 1/32 roll
            else:
                P["ohat"].append(N(b + 2.5, 0.35, 46, 70))
            if phrase_bar in (1, 3):                       # zaps
                P["zap"].append(N(b + 3.5, 0.2, 60, 96))
            if phrase_bar == 3 and s == "drop2":
                for i, t in enumerate((3.0, 3.25, 3.5, 3.75)):
                    P["zap"].append(N(b + t, 0.12, 60, 70 + i * 12))
        elif s == "break":
            if bar % 2 == 0:
                P["kick"].append(N(b, 0.3, 36, 96))
            P["clap"].append(N(b + 2.0, 0.3, 39, 70))
            for i in range(4):
                P["hat"].append(N(b + i + 0.5, 0.1, 42, 44))
        elif s == "outro":
            if bar == 76:
                P["kick"].append(N(b, 0.4, 36, 124))
                P["clap"].append(N(b, 0.4, 39, 120))
                P["ohat"].append(N(b, 1.5, 46, 100))
    return P


# ================================================================== bass ====
def bass():
    out = []
    for bar in range(BARS):
        b = bar * 4.0
        s = section(bar)
        r = root(bar)
        nxt = root(bar + 1) if bar + 1 < BARS else r
        if s == "verse":
            pat = [(0.0, 1.45, r, 110), (1.5, 0.45, r, 92), (2.5, 1.0, r, 104)]
            if bar % 4 == 3:                                    # slide up then back
                pat = [(0.0, 1.45, r, 110), (1.5, 0.45, r, 92), (2.5, 0.55, r, 104),
                       (3.0, 1.0, r + 12, 100)]
            out += [N(b + t, d, p, v) for t, d, p, v in pat]
        elif s == "pre":
            bb = bar - 24
            if bb < 4:
                out += [N(b, 1.9, r, 104), N(b + 2, 1.9, r, 100)]
            else:
                steps = 8 if bb < 7 else 6
                for i in range(steps):
                    out.append(N(b + i * 0.5, 0.42, r if i % 2 == 0 else r + 12, 96 + i * 2))
        elif s in ("drop1", "drop2"):
            pb = (bar - (32 if s == "drop1" else 60)) % 4
            if pb == 3:
                pat = [(0.0, 0.7, r, 118), (0.75, 0.7, r, 100), (1.5, 0.45, r, 110),
                       (2.0, 0.55, r, 112), (2.5, 0.55, r + 12, 106),   # glides up
                       (3.0, 0.55, r + 7, 104), (3.5, 0.45, nxt, 108)]  # ...and into the next root
            else:
                pat = [(0.0, 1.45, r, 120), (1.5, 0.45, r, 104), (2.0, 1.05, r, 114),
                       (3.0, 0.55, r + 12, 104), (3.5, 0.45, r, 100)]   # up-and-back wobble
            out += [N(b + t, d, p, v) for t, d, p, v in pat]
        elif s == "break":
            if bar >= 52:
                out.append(N(b, 3.8, r, 78))
        elif s == "build":
            bb = bar - 56
            step = [0.5, 0.5, 0.25, 0.25][bb]
            n = int(4 / step)
            for i in range(n):
                t = i * step
                if bb == 3 and t >= 3.0:
                    break
                out.append(N(b + t, step * 0.85, r if i % 2 == 0 else r + 12, 92 + 20 * t / 4))
        elif s == "outro" and bar == 76:
            out.append(N(b, 2.2, r, 124))                      # B1 ...
            out.append(N(b + 2.0, 3.5, r - 12, 110))           # ... glides down an octave
    return out


# ================================================================ chords ====
def saws():
    out = []
    for bar in range(BARS):
        b = bar * 4.0
        s = section(bar)
        v = voicing(bar)
        if s in ("drop1", "drop2"):
            pb = (bar - (32 if s == "drop1" else 60)) % 4
            if pb < 3 or (s == "drop2" and bar == 75):
                if pb == 3:   # final drop bar: straight 1/16 gate into the outro
                    for i in range(16):
                        out += [N(b + i * 0.25, 0.18, p, 88 + i * 2) for p in v]
                else:
                    out += [N(b, 3.92, p, 112) for p in v]
            else:             # every 4th bar: trance-gate stutter
                gate = [1, 0, 1, 1, 0, 1, 1, 0, 1, 0, 1, 1, 1, 1, 1, 1]
                for i, g in enumerate(gate):
                    if g:
                        out += [N(b + i * 0.25, 0.2, p, 96 + (14 if i % 4 == 0 else 0)) for p in v]
        elif s == "verse" and bar >= 16:
            for t in (0.5, 1.5, 2.5, 3.5):                     # offbeat stabs lift verse 2nd half
                out += [N(b + t, 0.28, p, 76) for p in v]
        elif s in ("pre", "build"):
            bb = bar - (24 if s == "pre" else 56)
            last = 7 if s == "pre" else 3
            if (s == "pre" and bb >= 4) or s == "build":
                prog = bb - (4 if s == "pre" else 0)
                step = [0.5, 0.5, 0.25, 0.125][prog]
                for i in range(int(4 / step)):
                    t = i * step
                    if bb == last and t >= 3.0:
                        break
                    out += [N(b + t, step * 0.7, p, 70 + 40 * (prog * 4 + t) / 16) for p in v]
        elif s == "outro":
            if bar == 76:
                out += [N(b, 3.9, p, 120) for p in v]
            elif bar == 77:                                    # 1/32 stutter, decaying
                for i in range(24):
                    out += [N(b + i * 0.125, 0.09, p, 110 - i * 3.6) for p in v]
    return out


def pad():
    out = []
    for bar in range(BARS):
        b = bar * 4.0
        s = section(bar)
        v = voicing(bar)
        if s == "intro":
            vel = 22 + (bar / 7) * 96                          # opens the filter
            out += [N(b, 3.95, p, vel) for p in v]
        elif s == "verse":
            out += [N(b, 3.95, p, 38) for p in v[1:4]]
        elif s == "pre":
            vel = 46 + (bar - 24) / 7 * 80
            dur = 3.95 if bar < 31 else 2.95
            out += [N(b, dur, p, vel) for p in v]
        elif s == "break":
            out += [N(b, 3.95, p, 74) for p in v]
        elif s == "build":
            dur = 3.95 if bar < 59 else 2.95
            out += [N(b, dur, p, 64 + (bar - 56) * 20) for p in v]
        elif s == "outro" and bar in (76, 77):
            out += [N(b, 3.95, p, 96 - (bar - 76) * 30) for p in v]
    return out


def pluck():
    out = []
    for bar in range(BARS):
        b = bar * 4.0
        s = section(bar)
        v = voicing(bar)
        up = 0 if s == "verse" else (24 if s in ("drop1", "drop2") else 12)
        tones = [v[1] + up, v[2] + up, v[3] + up, v[4] + up]
        seq = [tones[0], tones[1], tones[2], tones[3], tones[2], tones[1], tones[3], tones[2]]
        if (s == "intro" and bar >= 4) or s == "verse" or s == "break":
            vel0 = 70 if s != "intro" else 40 + (bar - 4) * 10
            for i, p in enumerate(seq):
                if s == "verse" and bar % 4 == 3 and i >= 6:
                    break
                out.append(N(b + i * 0.5, 0.35, p, vel0 + (10 if i % 2 == 0 else 0)))
        elif s in ("drop1", "drop2"):
            for i in range(16):
                p = seq[i % 8]
                out.append(N(b + i * 0.25, 0.16, p, 50 + (10 if i % 4 == 0 else 0)))
    return out


def bells():
    out = []
    for bar in range(BARS):
        b = bar * 4.0
        s = section(bar)
        v = voicing(bar)
        top = v[-1] + 12
        if s == "intro" and bar < 8:
            motif = [(0.0, top, 96), (1.5, v[-3] + 12, 78), (3.0, v[-2] + 12, 70)]
            out += [N(b + t, 1.2, p, vel) for t, p, vel in motif]
        elif s in ("drop1", "drop2") and bar % 2 == 0:
            out += [N(b, 1.5, top + 12, 92), N(b + 0.75, 1.0, v[-2] + 24, 70)]
        elif s == "break":
            for i, p in enumerate([v[2] + 24, v[3] + 24, v[4] + 24, v[3] + 24]):
                out.append(N(b + i, 1.4, p, 76 - i * 4))
        elif s == "outro" and bar == 78:
            out.append(N(b, 4.0, 95, 110))                     # B6: final ping
    return out


# ================================================================== lead ====
# (beat_in_bar, dur, pitch) for the 4-bar hook over F#m | D | A | E (A major)
HOOK_A = [
    [(0.0, .5, 81), (.5, .25, 81), (.75, .25, 80), (1.0, .5, 81), (1.5, .5, 85), (2.0, .5, 83),
     (2.5, .5, 81), (3.0, .75, 78), (3.75, .3, 76)],
    [(0.0, .5, 78), (.5, .25, 78), (.75, .25, 76), (1.0, .5, 78), (1.5, .5, 81), (2.0, 1.0, 78),
     (3.0, .5, 76), (3.5, .55, 73)],
    [(0.0, .5, 76), (.5, .25, 76), (.75, .25, 73), (1.0, .5, 76), (1.5, .5, 81), (2.0, .75, 85),
     (2.75, .25, 83), (3.0, .5, 81), (3.5, .55, 83)],
    [(0.0, .5, 83), (.5, .25, 83), (.75, .25, 81), (1.0, .5, 80), (1.5, .55, 76), (2.0, 1.5, 80),
     (3.5, .25, 73), (3.75, .3, 76)],
]
HOOK_B = [HOOK_A[0], HOOK_A[1],
          [(0.0, .5, 76), (.5, .25, 76), (.75, .25, 73), (1.0, .5, 76), (1.5, .5, 81), (2.0, .75, 88),
           (2.75, .3, 85), (3.0, .5, 83), (3.5, .55, 85)],
          [(0.0, 2.05, 83), (2.0, .5, 81), (2.5, .5, 80), (3.0, 1.0, 76)]]
# stutter ending ("vocal chop"): repeated 1/32 on the tonic
HOOK_END = [(0.0, .5, 83), (.5, .25, 83), (.75, .25, 81), (1.0, .5, 80), (1.5, .5, 76)] + \
           [(2.0 + i * .125, .1, (76, 80, 83, 88)[i // 4]) for i in range(16)]

VERSE = [
    [(0.5, .5, 73), (1.0, .5, 73), (1.5, .25, 71), (1.75, .25, 69), (2.0, .5, 71), (2.5, 1.0, 73)],
    [(0.5, .5, 69), (1.0, .5, 69), (1.5, .5, 66), (2.0, 1.5, 69)],
    [(0.5, .5, 73), (1.0, .5, 73), (1.5, .5, 76), (2.0, .5, 78), (2.5, 1.0, 76)],
    [(0.5, .5, 71), (1.0, .5, 68), (1.5, .5, 64), (2.0, 1.5, 68)],
]
VERSE_B = [
    [(0.5, .5, 73), (1.0, .5, 73), (1.5, .25, 76), (1.75, .25, 78), (2.0, .5, 76), (2.5, 1.0, 73)],
    [(0.5, .5, 69), (1.0, .5, 71), (1.5, .5, 73), (2.0, 1.5, 74)],
    [(0.5, .5, 76), (1.0, .5, 76), (1.5, .5, 78), (2.0, .5, 81), (2.5, 1.0, 78)],
    [(0.5, .5, 76), (1.0, .5, 71), (1.5, .5, 68), (2.0, 1.0, 71), (3.0, .5, 73), (3.5, .5, 76)],
]
BUILD = [  # over D | E | F# | F# (V of the new key)
    [(0.0, 1.0, 74), (1.0, 1.0, 78), (2.0, 1.0, 81), (3.0, 1.0, 85)],
    [(0.0, 1.0, 76), (1.0, 1.0, 80), (2.0, 1.0, 83), (3.0, 1.0, 88)],
    [(0.0, .5, 78), (0.5, .5, 82), (1.0, .5, 85), (1.5, .5, 90), (2.0, .5, 85), (2.5, .5, 82), (3.0, 1.0, 85)],
    [(0.0, 2.0, 85), (2.0, 0.9, 90)],
]
PRE = [  # climbing line over F#m D A E x2
    [(0.0, 1.0, 73), (1.0, 1.0, 76), (2.0, 1.0, 78), (3.0, 1.0, 81)],
    [(0.0, 1.5, 78), (1.5, .5, 76), (2.0, 2.0, 74)],
    [(0.0, 1.0, 76), (1.0, 1.0, 78), (2.0, 1.0, 81), (3.0, 1.0, 83)],
    [(0.0, 2.0, 80), (2.0, 2.0, 83)],
]


def lead():
    out = []

    def put(bar, cells, shift=0, vel=104, octave=0):
        b = bar * 4.0
        for t, d, p in cells:
            out.append(N(b + t, d, p + shift + octave, vel + (8 if t == 0.0 else 0)))

    for bar in range(BARS):
        s = section(bar)
        if s == "verse":
            ph = (bar - 8) // 4
            cells = (VERSE if ph % 2 == 0 else VERSE_B)[bar % 4]
            put(bar, cells, vel=92)
        elif s == "pre":
            put(bar, PRE[bar % 4], vel=96 + (bar - 24) * 3)
        elif s in ("drop1", "drop2"):
            i = bar - (32 if s == "drop1" else 60)
            sh = 2 if s == "drop2" else 0
            ph, pb = divmod(i, 4)
            if ph == 3 and pb == 3:
                put(bar, HOOK_END, sh, vel=110)
            else:
                put(bar, (HOOK_A if ph % 2 == 0 else HOOK_B)[pb], sh, vel=108)
        elif s == "build":
            put(bar, BUILD[bar - 56], vel=100 + (bar - 56) * 4)
    return out


def lead2():
    """Octave-up FM double: second half of drop 1, all of drop 2; echo in the break."""
    out = []
    for n in lead():
        bar = int(n["start"] // 4)
        s = section(bar)
        if (s == "drop1" and bar >= 40) or s == "drop2":
            out.append(N(n["start"], min(n["dur"], 0.45), n["pitch"] + 12, n["vel"] - 26))
    brk = [[(0, 1, 78), (1, 1, 81), (2, 2, 85)], [(0, 1, 80), (1, 1, 83), (2, 2, 88)],
           [(0, 1, 80), (1, 1, 76), (2, 2, 73)], [(0, 1, 78), (1, 1, 81), (2, 1.5, 85), (3.5, .5, 83)]]
    for bar in range(48, 56):                          # break: dreamy FM answer phrase
        cells = brk[bar % 4] if bar < 55 else [(0, 2, 78), (2, 2, 81)]
        for t, d, p in cells:
            out.append(N(bar * 4.0 + t, d, p, 84))
    return out


def riser():
    out = []
    for start_bar, bars, base in ((28, 4, 61), (56, 4, 61), (6, 2, 69)):
        total = bars * 4
        steps = []
        t = 0.0
        while t < total - 1.0:                         # stop before the gap beat
            frac = t / total
            step = 0.5 if frac < 0.5 else 0.25
            steps.append((t, step))
            t += step
        for i, (t, step) in enumerate(steps):
            frac = t / total
            p = base + int(frac * 24)                  # two octaves up, chromatic
            out.append(N(start_bar * 4 + t, step * 0.9, p, 30 + 97 * frac))
    return out


def noise():
    out = []
    for bar, dur, vel in ((6, 7.0, 90), (29, 11.0, 110), (57, 11.0, 118)):
        out.append(N(bar * 4.0, dur, 60, vel))
    return out


GAPS = [(127.0, 128.0), (239.0, 240.0)]   # silent beat before each drop


def gap_trim(notes):
    kept = []
    for n in notes:
        for g0, g1 in GAPS:
            if g0 <= n["start"] < g1:
                n = None
                break
            if n["start"] < g0 < n["start"] + n["dur"]:
                n = dict(n, dur=round(g0 - n["start"] - 0.02, 4))
        if n is not None and n["dur"] > 0.01:
            kept.append(n)
    return kept


def score():
    d = drums()
    parts = {"kick": d["kick"], "snare": d["snare"], "clap": d["clap"], "hat": d["hat"],
             "ohat": d["ohat"], "zap": d["zap"], "bass": bass(), "saws": saws(), "pad": pad(),
             "pluck": pluck(), "bells": bells(), "lead": lead(), "lead2": lead2(),
             "riser": riser(), "noise": noise()}
    parts = {k: gap_trim(v) for k, v in parts.items()}
    for name, notes in parts.items():
        notes.sort(key=lambda n: (n["start"], n["pitch"]))
        for n in notes:
            assert 0 <= n["start"] and n["start"] + n["dur"] <= CLIP_BEATS, (name, n)
            assert 0 <= n["pitch"] <= 127, (name, n)
    return parts


# ============================================================ sound design ====
# (engine, instrument params {name: value}, [(fx, {params})], track volume, stem)
POLY_OFF = {"Osc 3 Level": -60.0, "Osc 4 Level": -60.0, "Osc 3 Enable": 0.0, "Osc 4 Enable": 0.0}
DESIGN = {
    "kick": ("magda_kick", {"transient_level": 0.8, "transient_pitch": 300.0, "transient_sweep": 0.7,
                            "transient_decay": 10.0, "body_pitch": 46.0, "body_snap": 0.75,
                            "body_snap_time": 40.0, "body_decay": 300.0, "body_drive": 5.0,
                            "click_level": 0.45, "click_tone": 5000.0, "Gain": -3.0},
             [], 0.9, "drums"),
    "snare": ("magda_snare", {"transient_level": 0.8, "transient_pitch": 600.0, "transient_tone": 6000.0,
                              "body_tune": 210.0, "body_snap": 0.5, "body_decay": 140.0,
                              "rattle_snappy": 0.8, "rattle_tone": 5200.0, "rattle_hp_freq": 700.0,
                              "rattle_decay": 230.0, "rattle_drive": 4.0, "Gain": -6.0},
              [("magda_reverb", {"Engine": 2.0, "Mix": 0.14, "Decay": 35.0, "Low Cut": 250.0})], 0.7, "drums"),
    "clap": ("magda_clap", {"flam_tone": 1500.0, "flam_spread": 12.0, "flam_decay": 280.0, "flam_tail": 0.6,
                            "shape_hp_freq": 1200.0, "shape_drive": 4.0, "Gain": -6.0},
             [("magda_reverb", {"Engine": 0.0, "Mix": 0.2, "Decay": 40.0, "Low Cut": 400.0})], 0.75, "drums"),
    "hat": ("magda_hat", {"ring_level": 0.4, "ring_pitch": 800.0, "ring_spread": 1.4, "ring_decay": 12.0,
                          "noise_level": 0.7, "noise_hp_freq": 9500.0, "noise_decay": 45.0, "noise_sat": 0.3,
                          "Gain": -9.0},
            [("magda_utility", {"Pan": 0.22})], 0.6, "drums"),
    "ohat": ("magda_hat", {"ring_level": 0.35, "ring_pitch": 700.0, "ring_spread": 1.5, "ring_decay": 80.0,
                           "noise_level": 0.7, "noise_hp_freq": 8000.0, "noise_decay": 380.0, "noise_sat": 0.2,
                           "Gain": -12.0},
             [("magda_utility", {"Pan": -0.25})], 0.55, "drums"),
    "zap": ("magda_tom", {"body_tune": 380.0, "body_bend": 1.0, "body_decay": 140.0, "noise_level": 0.1,
                          "noise_tone": 6000.0, "noise_decay": 30.0, "Gain": -10.0},
            [("magda_bitcrusher", {"Bits": 6.0, "Rate": 12000.0, "Mix": 0.5}),
             ("magda_delay", {"Sync": 1.0, "Division": 2.0, "Feedback": 0.4, "Mix": 0.3, "Cross": 0.8})],
            0.5, "fx"),
    "bass": ("magda_polysynth", {"Osc 1 Wave": 0.0, "Osc 1 Level": 0.0, "Osc 2 Wave": 3.0, "Osc 2 Level": -14.0,
                                 "Osc 2 Coarse": 12.0, **POLY_OFF, "Filter Type": 0.0, "Cutoff": 900.0,
                                 "Resonance": 0.1, "Filter Drive": 0.55, "Filter Slope": 1.0,
                                 "Amp Attack": 2.0, "Amp Decay": 1200.0, "Amp Sustain": 0.6,
                                 "Amp Release": 90.0, "Voice Mode": 2.0, "Glide": 55.0, "Vel Amp": 0.4,
                                 "Output": -6.0},
             [("magda_saturator", {"Mode": 4.0, "Drive": 9.0, "Mix": 0.8, "Output": -3.0}),
              ("magda_eq", {"Band 1 Enabled": 1.0, "Band 1 Type": 0.0, "Band 1 Freq": 28.0, "Band 1 Q": 0.7})],
             0.8, "bass"),
    "saws": ("surge_xt", {"A Filter 1 Type": 0.03, "A Filter 1 Cutoff": 0.80, "A Filter 1 Resonance": 0.15,
                          "A Osc 1 Unison Voices": 0.45, "A Osc 1 Unison Detune": 0.25,
                          "A Amp EG Release": 0.42},
             [("magda_eq", {"Band 1 Enabled": 1.0, "Band 1 Type": 0.0, "Band 1 Freq": 170.0, "Band 1 Q": 0.7,
                            "Band 7 Enabled": 1.0, "Band 7 Type": 3.0, "Band 7 Freq": 9000.0,
                            "Band 7 Gain": 2.0}),
              ("magda_reverb", {"Engine": 1.0, "Mix": 0.16, "Decay": 55.0, "Low Cut": 300.0, "High Cut": 10000.0})],
             0.3, "synths"),
    "pad": ("magda_polysynth", {"Osc 1 Wave": 1.0, "Osc 1 Level": -10.0, "Osc 1 Fine": -12.0,
                                "Osc 2 Wave": 1.0, "Osc 2 Level": -10.0, "Osc 2 Fine": 12.0,
                                "Osc 3 Wave": 1.0, "Osc 3 Level": -14.0, "Osc 3 Coarse": 12.0, "Osc 3 Fine": -5.0,
                                "Osc 4 Wave": 2.0, "Osc 4 Level": -18.0, "Osc 4 Coarse": -12.0,
                                "Filter Type": 0.0, "Filter Slope": 1.0, "Cutoff": 380.0, "Resonance": 0.2,
                                "Vel Filter": 5.0, "Vel Amp": 0.25, "Amp Attack": 250.0, "Amp Decay": 800.0,
                                "Amp Sustain": 0.9, "Amp Release": 900.0, "Output": -12.0},
            [("magda_chorus", {"Voices": 1.0, "Rate": 0.4, "Depth": 0.5, "Mix": 0.45, "Width": 1.0}),
             ("magda_reverb", {"Engine": 1.0, "Mix": 0.3, "Decay": 65.0, "Low Cut": 250.0, "High Cut": 9000.0})],
            0.55, "synths"),
    "pluck": ("magda_polysynth", {"Osc 1 Wave": 1.0, "Osc 1 Level": -8.0, "Osc 2 Wave": 2.0, "Osc 2 Level": -12.0,
                                  "Osc 2 Coarse": 12.0, "Osc 2 Fine": 5.0, **POLY_OFF,
                                  "Filter Type": 0.0, "Cutoff": 700.0, "Resonance": 0.3, "Filter Env": 3.2,
                                  "Filter Attack": 1.0, "Filter Decay": 160.0, "Filter Sustain": 0.0,
                                  "Filter Release": 120.0, "Amp Attack": 1.0, "Amp Decay": 260.0,
                                  "Amp Sustain": 0.0, "Amp Release": 150.0, "Vel Amp": 0.6, "Vel Filter": 1.5,
                                  "Output": -10.0},
              [("magda_delay", {"Sync": 1.0, "Division": 7.0, "Feedback": 0.35, "Mix": 0.22, "Cross": 0.7}),
               ("magda_reverb", {"Engine": 0.0, "Mix": 0.15, "Decay": 40.0, "Low Cut": 300.0})],
              0.45, "synths"),
    "bells": ("magda_bell", {"strike_position": 0.4, "strike_tone": 9000.0, "strike_sharpness": 0.5,
                             "decay": 1800.0, "Gain": -8.0},
              [("magda_delay", {"Sync": 1.0, "Division": 7.0, "Feedback": 0.3, "Mix": 0.2, "Cross": 0.8}),
               ("magda_reverb", {"Engine": 1.0, "Mix": 0.35, "Decay": 60.0, "Low Cut": 400.0})],
              0.5, "synths"),
    "lead": ("magda_polysynth", {"Osc 1 Wave": 2.0, "Osc 1 Level": -8.0, "Osc 1 Fine": -6.0,
                                 "Osc 2 Wave": 1.0, "Osc 2 Level": -10.0, "Osc 2 Coarse": 12.0, "Osc 2 Fine": 7.0,
                                 "Osc 3 Wave": 1.0, "Osc 3 Level": -14.0, "Osc 3 Fine": 9.0,
                                 "Osc 4 Level": -60.0, "Osc 4 Enable": 0.0,
                                 "Filter Type": 0.0, "Cutoff": 5200.0, "Resonance": 0.25, "Filter Env": 1.0,
                                 "Filter Attack": 2.0, "Filter Decay": 250.0, "Filter Sustain": 0.5,
                                 "Filter Release": 200.0, "Filter Drive": 0.25, "Amp Attack": 3.0,
                                 "Amp Decay": 300.0, "Amp Sustain": 0.85, "Amp Release": 140.0,
                                 "Voice Mode": 2.0, "Glide": 45.0, "Vel Amp": 0.35, "Vel Filter": 1.0,
                                 "Output": -9.0},
             [("magda_saturator", {"Mode": 1.0, "Drive": 6.0, "Mix": 0.5}),
              ("magda_delay", {"Sync": 1.0, "Division": 7.0, "Feedback": 0.3, "Mix": 0.18, "Tone": 0.2,
                               "Cross": 0.6}),
              ("magda_reverb", {"Engine": 0.0, "Mix": 0.14, "Decay": 45.0, "Predelay": 15.0,
                                "Low Cut": 300.0, "High Cut": 11000.0})],
             0.6, "lead"),
    "lead2": ("magda_fm", {"Op1 Wave": 0.0, "Op1 Ratio": 1.0, "Op1 Level": 0.0, "M21": 1.6, "M31": 0.4,
                           "Op2 Ratio": 2.0, "Op3 Ratio": 3.0, "Op2 Level": -60.0, "Op3 Level": -60.0,
                           "Op4 Level": -60.0, "Amp Attack": 2.0, "Amp Decay": 400.0, "Amp Sustain": 0.4,
                           "Amp Release": 250.0, "Gain": -10.0},
              [("magda_bitcrusher", {"Bits": 7.0, "Rate": 16000.0, "Mix": 0.35, "Drive": 3.0}),
               ("magda_delay", {"Sync": 1.0, "Division": 5.0, "Feedback": 0.35, "Mix": 0.25, "Cross": 0.8}),
               ("magda_reverb", {"Engine": 0.0, "Mix": 0.2, "Decay": 50.0, "Low Cut": 400.0})],
              0.4, "lead"),
    "riser": ("magda_polysynth", {"Osc 1 Wave": 1.0, "Osc 1 Level": -8.0, "Osc 2 Wave": 1.0, "Osc 2 Level": -8.0,
                                  "Osc 2 Coarse": 12.0, "Osc 2 Fine": 15.0, **POLY_OFF,
                                  "Filter Type": 0.0, "Cutoff": 400.0, "Resonance": 0.35, "Vel Filter": 5.5,
                                  "Amp Attack": 5.0, "Amp Sustain": 1.0, "Amp Release": 200.0, "Vel Amp": 0.7,
                                  "Output": -14.0},
              [("magda_delay", {"Sync": 1.0, "Division": 2.0, "Feedback": 0.3, "Mix": 0.2, "Cross": 0.8}),
               ("magda_reverb", {"Engine": 1.0, "Mix": 0.35, "Decay": 60.0, "Low Cut": 300.0})],
              0.5, "fx"),
    "noise": ("magda_fm", {"Op1 Wave": 4.0, "Op1 Level": 0.0, "M21": 0.0, "Op2 Level": -60.0,
                           "Op3 Level": -60.0, "Op4 Level": -60.0, "Amp Attack": 1800.0, "Amp Decay": 2000.0,
                           "Amp Sustain": 1.0, "Amp Release": 400.0, "Vel Amount": 1.0, "Gain": -14.0},
              [("magda_filter", {"Mode": 2.0, "Cutoff": 1500.0, "Resonance": 0.2}),
               ("magda_reverb", {"Engine": 1.0, "Mix": 0.4, "Decay": 60.0, "Low Cut": 500.0})],
              0.45, "fx"),
}
STEMS = ["drums", "bass", "synths", "lead", "fx"]
MIX_TRIM = 0.5            # -6 dB on every fader: headroom so no render clips


# ================================================================== build ====
async def set_params(daw, track_id, device, params, report, label):
    """Set by name with range clamping, then read back and record mismatches."""
    devs = await daw.get_devices_full(track_id)
    dev = next(d for d in devs if str(d["id"]) == str(device))
    meta = {p["name"]: p for p in dev.get("parameters", [])}
    for name, value in params.items():
        if name not in meta:
            report["problems"].append(f"{label}: no parameter {name!r}")
            continue
        lo, hi = float(meta[name].get("min", 0.0)), float(meta[name].get("max", 1.0))
        v = min(hi, max(lo, float(value)))
        try:
            await daw.set_parameter(track_id, dev["id"], name, v)
        except Exception as e:  # noqa: BLE001
            report["problems"].append(f"{label}: set {name} failed: {e}")
    devs = await daw.get_devices_full(track_id)
    dev = next(d for d in devs if str(d["id"]) == str(device))
    got = {p["name"]: p.get("value") for p in dev.get("parameters", [])}
    for name, value in params.items():
        if name in got and got[name] is not None and abs(float(got[name]) - float(value)) > 1e-3 * max(1.0, abs(float(value))):
            report["readback_mismatch"].append({"device": label, "param": name, "wanted": value, "got": got[name]})
    report["devices"].append({"track": label, "device_id": dev["id"], "name": dev.get("name"),
                              "params_set": len(params)})


async def main():
    parts = score()
    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "stems").mkdir(parents=True, exist_ok=True)
    (OUT / "score.json").write_text(json.dumps({"bpm": BPM, "bars": BARS, "sections": SECTIONS,
                                                "render_beats": RENDER_BEATS, "parts": parts}))
    log("score:", {k: len(v) for k, v in parts.items()})
    report = {"problems": [], "readback_mismatch": [], "devices": [], "tracks": {}, "renders": {},
              "timings": {}}

    daw = MagdaServeAdapter(CLI, WORK / "glitter_rush.mgd")
    t = time.time()
    await daw.initialize()
    await daw._ensure_session()
    report["timings"]["boot"] = round(time.time() - t, 1)
    try:
        await daw._exec("set-tempo", str(BPM))
        plugins = {p["name"]: p for p in await daw.list_plugins()}
        tids = {}
        t = time.time()
        for name, (engine, iparams, fx, vol, stem) in DESIGN.items():
            tr = await daw.create_track(name)
            tids[name] = tr.id
            if engine == "surge_xt":
                dev = await daw.add_plugin(tr.id, plugins["Surge XT"]["id"], as_instrument=True)
                await asyncio.sleep(4.0)                       # async VST3 init
                for pname, val in iparams.items():
                    await daw.set_external_parameter(dev, pname, float(val))
                back = {p["name"]: p["value"] for p in await daw.external_parameters(dev)}
                for pname, val in iparams.items():
                    if abs(back.get(pname, -1) - val) > 1e-3:
                        report["readback_mismatch"].append({"device": name, "param": pname,
                                                            "wanted": val, "got": back.get(pname)})
                report["devices"].append({"track": name, "device_id": dev, "name": "Surge XT",
                                          "params_set": len(iparams)})
            else:
                await daw.load_device(tr.id, engine)
                inst = (await daw.get_devices_full(tr.id))[0]
                await set_params(daw, tr.id, inst["id"], iparams, report, f"{name}/{engine}")
            for fx_name, fx_params in fx:
                fid = await daw.add_fx(tr.id, fx_name)
                await set_params(daw, tr.id, fid, fx_params, report, f"{name}/{fx_name}")
            vol = round(vol * MIX_TRIM, 4)
            await daw.set_track_volume(tr.id, vol)
            report["tracks"][name] = {"id": tr.id, "engine": engine, "stem": stem, "volume": vol,
                                      "fx": [f for f, _ in fx]}
        report["timings"]["sound_design"] = round(time.time() - t, 1)
        log("tracks + devices ready", report["timings"])

        t = time.time()
        for name, notes in parts.items():
            tid = tids[name]
            clip = await daw.create_clip(tid, name, 0.0, CLIP_BEATS)
            cid = str(clip.id).removeprefix("clip:")
            for i in range(0, len(notes), 120):
                cmds = []
                for n in notes[i:i + 120]:
                    cmds += ["add-midi-note", cid, f"{n['start']:.6f}", str(n["pitch"]),
                             f"{n['dur']:.6f}", str(n["vel"])]
                await daw._exec(*cmds)
            live = await daw.get_notes(tid)
            report["tracks"][name]["notes_written"] = len(notes)
            report["tracks"][name]["notes_live"] = len(live)
            if len(live) != len(notes):
                report["problems"].append(f"{name}: wrote {len(notes)} notes, project has {len(live)}")
        report["timings"]["notes"] = round(time.time() - t, 1)
        log("notes written", {k: v.get("notes_live") for k, v in report["tracks"].items()})
        await daw.save_project()
        (OUT / "session.json").write_text(json.dumps(report, indent=1, default=str))

        async def render(path, label):
            t = time.time()
            await asyncio.wait_for(daw.render(RenderRequest(output_path=path, start_beats=0.0,
                                                            length_beats=RENDER_BEATS)), 900)
            st = analyze_wav(path).to_dict()
            st["render_seconds"] = round(time.time() - t, 1)
            report["renders"][label] = st
            log("render", label, st)
            (OUT / "session.json").write_text(json.dumps(report, indent=1, default=str))

        await render(OUT / "mix_raw.wav", "mix")
        for stem in STEMS:
            for name, info in report["tracks"].items():
                await daw.set_track_volume(info["id"], info["volume"] if info["stem"] == stem else 0.0)
            await render(OUT / "stems" / f"{stem}.wav", stem)
        for name, info in report["tracks"].items():               # restore the mix
            await daw.set_track_volume(info["id"], info["volume"])
        await daw.save_project()
    finally:
        await daw.shutdown()
        report["timings"]["total"] = round(time.time() - T0, 1)
        (OUT / "session.json").write_text(json.dumps(report, indent=1, default=str))
        log("done", report["timings"], "problems:", report["problems"][:10],
            "mismatches:", len(report["readback_mismatch"]))


if __name__ == "__main__":
    asyncio.run(main())
