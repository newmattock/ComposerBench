"""AFTERGLOW: melodic house built on the full ComposerBench pipeline.

122 BPM, A minor, 72 bars (~2:22). A marimba arp and warm pad open it; plucks
and a four-on-the-floor kick build; the drops add Surge XT supersaw stabs, an
offbeat 808 and a glide-lead hook; the second drop doubles the hook an octave
down.

    intro 0 (8)  build1 8 (8)  drop1 16 (16)  breakdown 32 (8)
    build2 40 (8)  drop2 48 (16)  outro 64 (8)

Pipeline: compose() -> check_score -> song_spec() with levels (no hand-set
faders) -> build_song -> balance_song(apply) -> mix_spec() -> run_mix.

    python examples/afterglow.py [--out DIR] [--no-plugins] [--check] [--mix-only]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from composerbench.analysis.harmony import check_score  # noqa: E402
from composerbench.composition.patterns import arpeggio, chord_notes, hits, humanize, legato, note, progression, ramp  # noqa: E402
from composerbench.composition.score import Score  # noqa: E402

BPM = 122
SECTIONS = [  # name, bars, kind, key shift, energy
    ("intro", 8, "intro", 0, 0.25), ("build1", 8, "build", 0, 0.5), ("drop1", 16, "drop", 0, 0.9),
    ("breakdown", 8, "breakdown", 0, 0.35), ("build2", 8, "build", 0, 0.65), ("drop2", 16, "drop", 0, 1.0),
    ("outro", 8, "outro", 0, 0.25),
]
MAIN = ["Am", "F", "C", "G"]
BREAK = ["Fmaj7", "G", "Em7", "Am"]
ROOTS = {"Am": 33, "F": 29, "C": 36, "G": 31, "Fmaj7": 29, "Em7": 28}   # 808 roots (A1, F1, C2, G1, E1)

# the hook over Am F C G, as (beat in the 4-bar cycle, length, pitch, slide into next)
HOOK_A = [(0, 1, 76, 0), (1, .5, 72, 0), (1.5, .5, 74, 1), (2, 1.5, 76, 0),
          (4, 1, 81, 0), (5, .5, 79, 0), (5.5, .5, 77, 1), (6, 1.5, 76, 0),
          (8, .5, 79, 0), (8.5, .5, 76, 0), (9, .5, 79, 1), (9.5, 1.5, 84, 0),
          (12, 1, 83, 0), (13, .5, 81, 0), (13.5, .5, 79, 1), (14, 1.5, 74, 0)]
HOOK_B = HOOK_A[:12] + [(12, .5, 83, 0), (12.5, 1, 86, 1), (13.5, .5, 83, 0), (14, 2, 81, 0)]


def bar(sc: Score, section: str, b: int, step: float = 0.0) -> float:
    return sc.section(section).start + 4 * b + step


def compose() -> Score:
    sc = Score.from_bars(BPM, SECTIONS, title="afterglow", key="A minor")
    for s in sc.sections:
        prog = BREAK if s.kind == "breakdown" else MAIN
        sc.chords_from_bars([prog[b % 4] for b in range(int((s.end - s.start) // 4))], start_bar=int(s.start // 4))
    pads = dict(zip(MAIN, progression(MAIN, 52, 72, 4)))
    pads.update(zip(BREAK, progression(BREAK, 52, 72, 4)))
    keys = dict(zip(MAIN + BREAK, progression(MAIN + BREAK, 60, 79, 3)))

    for s in sc.sections:
        prog = BREAK if s.kind == "breakdown" else MAIN
        bars = int((s.end - s.start) // 4)
        for b in range(bars):
            t, sym = s.start + 4 * b, prog[b % 4]
            # pad: whole bars everywhere but the drops' second halves; velocity opens the filter
            if s.kind in ("intro", "build", "breakdown", "outro") or b % 8 < 4:
                sc.add("pad", chord_notes(pads[sym], t, 3.95, 70 if s.kind != "drop" else 60))
            # marimba arp: 16ths over three voices, two octaves
            if s.kind != "build" or b >= 4:
                order = [0, 2, 4, 1, 3, 5, 2, 4] * 2
                vel = 70 if s.kind in ("intro", "outro", "breakdown") else 84
                sc.add("marimba", arpeggio(keys[sym], order, t, 0.25, 0.22, vel, octaves=2, accent_every=4))
            # pluck: syncopated stabs from the first build on
            if s.kind in ("build", "drop"):
                for k, st in enumerate((0, 0.75, 1.5, 2.5, 3.25)):
                    sc.add("pluck", chord_notes(keys[sym], t + st, 0.3, 88 if k == 0 else 76))
            # Surge supersaw stabs on the offbeats in the drops, held chord on the 4th bar
            if s.kind == "drop":
                if b % 4 == 3:
                    sc.add("saws", chord_notes(pads[sym], t, 3.5, 96))
                else:
                    for st in (0.5, 1.5, 2.5, 3.5):
                        sc.add("saws", chord_notes(pads[sym], t + st, 0.42, 100))
            # bass: offbeat 808 in builds/drops (root on 1 in drops), sub in the breakdown
            root = ROOTS[sym]
            if s.kind == "drop":
                sc.add("bass", [note(t, 0.45, root, 118)] + [note(t + st, 0.4, root, 104) for st in (1.5, 2.5, 3.5)])
            elif s.kind == "build" and b >= 2:
                sc.add("bass", [note(t + st, 0.35, root + 12 if b % 2 else root, 96) for st in (0.5, 1.5, 2.5, 3.5)])
            elif s.kind == "breakdown":
                sc.add("sub", [note(t, 3.9, root + 12, 92)])
            # drums
            if s.kind in ("build", "drop") or (s.kind == "outro" and b < 4):
                sc.add("kick", hits("x...x...x...x...", t, 36, 112 if s.kind == "drop" else 100))
            if s.kind == "drop" or (s.kind == "build" and b >= 4):
                sc.add("clap", hits("....x.......x...", t, 39, 104))
            if s.kind != "breakdown":
                hat = "..x...x...x...x." if s.kind in ("intro", "outro") else "..x.o.x...x.o.xo"
                sc.add("hat", hits(hat, t, 42, 92 if s.kind == "drop" else 76))
            if s.kind == "drop":
                sc.add("ohat", hits("..x...x...x...x.", t, 46, 70))
        # section moves
        if s.kind == "build":
            last = s.start + 4 * (bars - 2)
            roll = hits("x.x.x.x.x.x.x.x.", last, 38, 70) + hits("xxxxxxxxrrrrRRRR", last + 4, 38, 90)
            sc.add("snare", ramp(roll, 40, 120))
            sc.add("riser", [note(s.start + 4 * (bars - 4), 15.75, 60, 120)])
            sc.add("swell", [note(s.end - 2, 1.9, 60, 115)])
            sc.parts["pad"] = ramp(sc.parts["pad"], 40, 110, s.start, s.end) if s.name == "build2" else sc.parts["pad"]
        if s.kind == "drop":
            sc.add("crash", [note(s.start, 2, 49, 110), note(s.start + 32, 2, 49, 96)])
            sc.add("impact", [note(s.start, 2, 36, 120)])
            hook = HOOK_A + HOOK_B
            for rep in range(bars // 8):
                base = s.start + 32 * rep
                line = [note(base + st + (16 if i >= 16 else 0), ln, p, 104) for i, (st, ln, p, _sl) in enumerate(hook)]
                slides = [bool(h[3]) for h in hook]
                sc.add("lead", legato(line, slides))
                if s.name == "drop2":
                    sc.add("lead_lo", legato([{**n, "pitch": n["pitch"] - 12, "velocity": 92} for n in line], slides))
        if s.kind == "breakdown":
            # same hook over Fmaj7-G-Em7-Am: the C6 would rub against Em7's B, so it lands on B5
            line = [note(s.start + 16 + st, ln, 83 if p == 84 else p, 78) for st, ln, p, _ in HOOK_A]
            sc.add("lead", legato(line, [bool(h[3]) for h in HOOK_A]))
            sc.add("bells", arpeggio(keys["Fmaj7"] + [84], [0, 2, 3, 1], s.start, 0.5, 0.4, 72, octaves=2)[:16])
            sc.add("riser", [note(s.end - 8, 7.75, 64, 100)])
    for part in ("marimba", "hat"):
        sc.parts[part] = humanize(sc.parts[part], timing=0.008, velocity=6, seed=122)
    sc.parts["lead"] = sorted(sc.parts["lead"], key=lambda n: n["start"])
    return sc.sort()


def song_spec(score_path: str, plugins: bool = True) -> dict:
    # levels only: LU relative to the kick; presets bring theirs, the balance pass trims the rest
    saws = "supersaw_surge" if plugins else "supersaw_poly"
    return {
        "title": "afterglow", "score": score_path,
        "tracks": [
            {"name": "kick", "preset": "kick_punch", "params": {"body_decay": 380.0}},
            {"name": "clap", "preset": "clap_wide", "level": -5.0},
            {"name": "snare", "preset": "snare_tight", "level": -6.0},
            {"name": "hat", "preset": "hat_closed", "pan": 0.15},
            {"name": "ohat", "preset": "hat_open", "pan": -0.15},
            {"name": "bass", "preset": "bass_808", "level": -2.0},
            {"name": "sub", "preset": "sub_sine", "level": -6.0},
            {"name": "pad", "preset": "pad_sweep", "level": -5.0},
            {"name": "saws", "preset": saws, "level": -2.0},
            {"name": "pluck", "preset": "pluck", "level": -6.0, "pan": -0.25},
            {"name": "marimba", "stem": "keys", "level": -5.0, "pan": 0.2,
             "instrument": {"device": "magda_marimba", "params": {
                 "strike_position": 0.35, "strike_tone": 5200.0, "strike_sharpness": 0.35, "decay": 420.0,
                 "Gain": -6.0}},
             "fx": [{"device": "magda_delay", "params": {"Sync": "On", "Division": "1/8.", "Feedback": 0.3,
                                                         "Mix": 0.18, "Cross": 0.7}},
                    {"device": "magda_reverb", "params": {"Engine": "Hall", "Mix": 0.25, "Decay": 55.0,
                                                          "Low Cut": 300.0}}]},
            {"name": "bells", "preset": "bell", "level": -9.0, "pan": 0.3},
            {"name": "lead", "preset": "lead_glide", "level": 0.0,
             "params": {"Cutoff": 4200.0, "Glide": 45.0, "Amp Attack": 6.0}},
            {"name": "lead_lo", "preset": "lead_glide", "stem": "lead", "level": -6.0,
             "params": {"Cutoff": 2400.0, "Glide": 45.0}},
            {"name": "riser", "preset": "riser"},
            {"name": "swell", "preset": "noise_swell"},
            {"name": "crash", "preset": "crash_noise"},
            {"name": "impact", "preset": "impact_boom", "level": -7.0},
        ],
        "render": {"stems": True, "tail_beats": 8, "bit_depth": 24},
    }


def mix_spec() -> dict:
    opening = {"lowpass_hz": [[0, 700], ["intro.end-1", 3000], ["intro.end", 20000, "jump"],
                              ["breakdown", 1800, "jump"], ["breakdown+24", 2500], ["breakdown.end", 20000, "jump"],
                              ["outro", 20000], ["outro.end", 900]]}
    lift = {"highpass_hz": [[0, 20], ["build1.end-1", 20, "jump"], ["build1.end-0.05", 700], ["build1.end", 20, "jump"],
                            ["build2.end-1", 20, "jump"], ["build2.end-0.05", 900], ["build2.end", 20, "jump"]]}
    return {
        "score": "score.json", "stems_dir": "stems",
        "stems": {
            "drums": {"automation": {"lowpass_hz": [[0, 20000], ["build2", 2500, "jump"], ["build2.end-0.5", 20000]]}},
            "bass": {"mono_below_hz": 140},
            "synths": {"automation": {**opening, **lift}, "eq": [{"type": "highpass", "freq": 140}], "width": 1.3},
            "keys": {"automation": {"lowpass_hz": opening["lowpass_hz"]}, "eq": [{"type": "highpass", "freq": 200}]},
            "lead": {"eq": [{"type": "highpass", "freq": 200}, {"type": "bell", "freq": 3000, "q": 1.0, "gain_db": 1.5}]},
            "fx": {},
        },
        "sidechain": [{"trigger": {"part": "kick"}, "targets": ["bass", "synths", "keys"], "depth_db": 4,
                       "section_depth_db": {"drop1": 7, "drop2": 8}, "release_ms": 160}],
        "cuts": [{"start": "build1.end-0.5", "end": "build1.end", "keep": ["fx"]},
                 {"start": "build2.end-1", "end": "build2.end", "keep": ["fx"]}],
        "edits": [{"type": "reverse_swell", "start": "drop2-2", "end": "drop2", "source_start": "drop2",
                   "stems": ["synths"], "gain_db": -8}],
        "bus": {"eq": [{"type": "highpass", "freq": 28, "order": 4}],
                "glue": {"threshold_db": -14, "ratio": 2, "attack_ms": 15, "release_ms": 180}, "mono_below_hz": 120},
        "master": {"target_lufs": -10, "ceiling_dbtp": -1.0, "fade_in": 0.5, "fade_out": 6, "end": "outro.end+6"},
        "export": {"basename": "afterglow_master", "mp3": True, "spectrogram": True},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path.home() / "Music" / "ComposerBench" / "afterglow"))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--mix-only", action="store_true")
    ap.add_argument("--no-plugins", action="store_true", help="built-in supersaw instead of Surge XT")
    ap.add_argument("--engine", help="magda_cli path (default: discovered)")
    ns = ap.parse_args(argv)
    out = Path(ns.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    from composerbench.mixing.balance import balance_song
    from composerbench.mixing.mixdown import run_mix
    from composerbench.song.build import build_song, check_song
    (out / "mix.json").write_text(json.dumps(mix_spec(), indent=1))
    if not ns.mix_only:
        score = compose()
        checks = check_score(score, ranges={"bass": (24, 52), "lead": (64, 96), "lead_lo": (52, 84)},
                             mono=["bass", "lead", "lead_lo"])
        print("score:", checks["harmony"]["summary"], "|", checks["ranges"]["summary"], "|",
              checks["sustain"]["summary"], "| problems:", checks["validation"][:3] or "none", flush=True)
        for c in checks["harmony"]["clashes"][:8] + checks["ranges"]["issues"][:8] + checks["sustain"]["issues"][:8]:
            print("  -", c)
        score.save(out / "composition.json")
        spec = song_spec(str(out / "composition.json"), plugins=not ns.no_plugins)
        (out / "song.json").write_text(json.dumps(spec, indent=1))
        pre = check_song(spec, out)
        print("preflight:", "ok" if pre["ok"] else "FAILED", *pre["info"], flush=True)
        for line in pre["errors"] + pre["warnings"]:
            print("  -", line)
        if ns.check or not pre["ok"]:
            return 0 if pre["ok"] else 1
        report = asyncio.run(build_song(spec, out_dir=out, cli_path=ns.engine))
        print("build problems:", report["problems"][:12] or "none", "| stems:", report.get("stem_method"), flush=True)
        bal = asyncio.run(balance_song(out, cli_path=ns.engine, apply=True))
        moves = ", ".join(f"{r['track']} {r['gain_db']:+.1f}" for r in bal["tracks"] if abs(r["gain_db"]) >= 1)
        print(f"balance: {moves or 'all within 1 dB'} (reference shift {bal['reference_shift_db']} dB)", flush=True)
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
