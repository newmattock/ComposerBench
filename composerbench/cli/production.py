"""CLI verbs for the production layer (all print JSON on stdout):

    composerbench song example > song.json         a starter spec (8 bars, presets)
    composerbench song check song.json             preflight: devices, params, notes, disk
    composerbench song build song.json [--out DIR] [--stems all|none|drums,bass] [--force]
    composerbench song balance SONG_DIR [--apply] [--levels '{"pad": -4}']   per-track loudness -> faders
    composerbench mix example > mix.json           a starter mix spec
    composerbench mix mix.json [--out DIR]         stems -> master.wav/mp3 + report
    composerbench devices [NAME] [--kind instrument|effect]
    composerbench devices refresh                  re-probe the engine, update the user catalog
    composerbench presets [NAME]
    composerbench presets calibrate [--engine PATH] [--only a,b]   measure preset loudness at unity
    composerbench score check score.json [--ranges '{"bass":[24,52]}'] [--mono bass,lead]
    composerbench report mix.wav [--score score.json] [--stems DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, default=str))


def _stems_arg(value: str | None):
    if value is None:
        return None
    if value.lower() in ("all", "true", "yes"):
        return True
    if value.lower() in ("none", "false", "no", ""):
        return False
    return [s.strip() for s in value.split(",") if s.strip()]


async def cmd_song(ns: argparse.Namespace) -> int:
    from composerbench.song.build import build_song, check_song
    if ns.song_action == "example":
        from composerbench.song.examples import example_song
        spec, score = example_song()
        if ns.score_out:
            Path(ns.score_out).write_text(json.dumps(score))
            spec["score"] = ns.score_out
        else:
            spec["score"] = score
        _print(spec)
        return 0
    if not ns.spec:
        print("a spec file is required", file=sys.stderr)
        return 2
    if ns.song_action == "balance":
        from composerbench.mixing.balance import balance_song
        levels = json.loads(ns.levels) if ns.levels else None
        report = await balance_song(ns.spec, cli_path=ns.engine, apply=ns.apply, levels=levels,
                                    progress=lambda stage, info=None: print(f"[balance] {stage} {info or ''}",
                                                                            file=sys.stderr, flush=True))
        _print({**{k: report[k] for k in ("song", "reference_lufs", "reference_shift_db", "applied")},
                "tracks": [{k: r.get(k) for k in ("track", "volume", "new_volume", "gain_db", "measured_lufs",
                                                  "target_lufs", "note")} for r in report["tracks"]]})
        return 0
    if ns.song_action == "check":
        report = check_song(ns.spec, ns.out)
        _print(report)
        return 0 if report["ok"] else 1
    report = await build_song(ns.spec, out_dir=ns.out, cli_path=ns.engine,
                              stems=_stems_arg(ns.stems), force=ns.force)
    _print({k: report[k] for k in ("title", "out_dir", "renders", "problems", "readback_mismatch", "timings")})
    return 0


async def cmd_mix(ns: argparse.Namespace) -> int:
    if ns.spec == "example":
        from composerbench.mixing.spec import example_spec
        _print(example_spec())
        return 0
    from composerbench.mixing.mixdown import run_mix
    report = run_mix(ns.spec, ns.out)
    keep = ("files", "master", "premaster", "applied", "notes", "bus", "seconds_to_mix")
    out = {k: report.get(k) for k in keep}
    if report.get("sections"):
        out["section_lufs"] = report["sections"]["contour"]
        out["flags"] = report["sections"]["flags"]
    _print(out)
    return 0


async def cmd_devices(ns: argparse.Namespace) -> int:
    from composerbench.devices import catalog
    if ns.name == "refresh":
        from composerbench.devices.build_catalog import refresh_live
        from composerbench.engine import discover_cli
        cli = discover_cli(ns.engine)
        if cli is None:
            print(json.dumps({"error": "magda_cli not found; pass --engine"}))
            return 1
        work = Path.home() / "Music" / "ComposerBench" / "_device_refresh"
        _print(await refresh_live(cli, work))
        return 0
    if ns.name:
        entry = catalog.device(ns.name)
        if entry is None:
            _print({"error": f"{ns.name} is not in the catalog",
                    "devices": [d["name"] for d in catalog.list_devices()]})
            return 1
        if ns.text:
            print(catalog.describe(ns.name))
        else:
            _print(entry)
        return 0
    _print(catalog.list_devices(ns.kind))
    return 0


async def cmd_presets(ns: argparse.Namespace) -> int:
    from composerbench.devices.presets import get_preset, list_presets
    if ns.name == "calibrate":
        from composerbench.devices import levels
        from composerbench.engine import require_cli
        work = Path.home() / "Music" / "ComposerBench" / "_preset_calibration"
        only = [n for n in (ns.only or "").split(",") if n] or None
        result = await levels.calibrate(require_cli(ns.engine), work, presets=only)
        if only:   # merge a partial run into the existing file
            merged = levels.load_calibration()
            merged.setdefault("presets", {}).update(result["presets"])
            result = {**merged, "generated": result["generated"], "engine": result["engine"]}
        levels.CALIBRATION_FILE.write_text(json.dumps(result, indent=1) + "\n")
        _print({"written": str(levels.CALIBRATION_FILE), "presets": result["presets"],
                "problems": result.get("problems", [])})
        return 0
    _print(get_preset(ns.name) if ns.name else list_presets())
    return 0


async def cmd_score(ns: argparse.Namespace) -> int:
    from composerbench.analysis.harmony import check_score
    from composerbench.composition.score import Score
    score = Score.load(ns.score)
    ranges = {k: tuple(v) for k, v in json.loads(ns.ranges).items()} if ns.ranges else None
    mono = [m for m in (ns.mono or "").split(",") if m]
    _print(check_score(score, ranges, mono))
    return 0


async def cmd_report(ns: argparse.Namespace) -> int:
    from composerbench.analysis.sections import section_report
    from composerbench.analysis.wav import read_wav
    from composerbench.mixing.dsp import as_stereo
    from composerbench.mixing.loudness import loudness_summary
    x, sr = read_wav(ns.wav)
    x = as_stereo(x)
    sections = []
    if ns.score:
        sc = json.loads(Path(ns.score).read_text())
        k = 60.0 / sc["bpm"]
        sections = [{"name": s["name"], "start_s": s["start"] * k, "end_s": s["end"] * k}
                    for s in sc.get("sections", [])]
    stems = {}
    if ns.stems:
        for p in sorted(Path(ns.stems).glob("*.wav")):
            y, _ = read_wav(p)
            stems[p.stem] = as_stereo(y)
    _print({"summary": loudness_summary(x, sr),
            "sections": section_report(x, sr, sections, stems) if sections else None})
    return 0


def register(sub) -> None:
    song = sub.add_parser("song", help="declarative song build: spec -> MAGDA project + renders")
    song.add_argument("song_action", choices=["example", "check", "build", "balance"])
    song.add_argument("spec", nargs="?", help="spec file (check/build) or built song folder (balance)")
    song.add_argument("--apply", action="store_true", help="balance: write the faders and rebuild the stems")
    song.add_argument("--levels", help='balance: target levels per track, JSON (LU relative to the kick)')
    song.add_argument("--out", help="output folder (default ~/Music/ComposerBench/<title>)")
    song.add_argument("--engine", help="magda_cli path (default: discovered)")
    song.add_argument("--stems", help="all | none | comma-separated stem names")
    song.add_argument("--force", action="store_true", help="build even if preflight reports errors")
    song.add_argument("--score-out", help="example: write the score here instead of inline")
    song.set_defaults(func=cmd_song)

    mix = sub.add_parser("mix", help="stems + mix spec -> master + report ('mix example' for a starter)")
    mix.add_argument("spec")
    mix.add_argument("--out", help="output folder (default: the spec's folder)")
    mix.set_defaults(func=cmd_mix)

    dev = sub.add_parser("devices", help="MAGDA device catalog: parameters, ranges, labels")
    dev.add_argument("name", nargs="?", help="device to describe, or 'refresh'")
    dev.add_argument("--kind", choices=["instrument", "effect"])
    dev.add_argument("--text", action="store_true", help="human-readable description")
    dev.add_argument("--engine", help="magda_cli path for 'refresh'")
    dev.set_defaults(func=cmd_devices)

    pre = sub.add_parser("presets", help="verified track presets (instrument + FX + level + stem)")
    pre.add_argument("name", nargs="?", help="preset to show, or 'calibrate'")
    pre.add_argument("--engine", help="magda_cli path for 'calibrate'")
    pre.add_argument("--only", help="calibrate: comma-separated presets (merged into the existing file)")
    pre.set_defaults(func=cmd_presets)

    sc = sub.add_parser("score", help="symbolic checks on a score.json")
    sc.add_argument("score_action", choices=["check"])
    sc.add_argument("score")
    sc.add_argument("--ranges", help='JSON, e.g. {"bass": [24, 52]}')
    sc.add_argument("--mono", help="comma-separated parts that must not overlap")
    sc.set_defaults(func=cmd_score)

    rep = sub.add_parser("report", help="loudness + per-section measurements of a WAV")
    rep.add_argument("wav")
    rep.add_argument("--score", help="score.json for section boundaries")
    rep.add_argument("--stems", help="folder of stem WAVs to break down per section")
    rep.set_defaults(func=cmd_report)
