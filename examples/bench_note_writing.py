"""Benchmark: how long does it take to write N notes into one clip?

    .venv/bin/python examples/bench_note_writing.py            # default sizes
    .venv/bin/python examples/bench_note_writing.py 500 5040   # custom sizes

Variants (each on a fresh track + clip in one serve session):
    chunked      64 notes per exec, saving after each (how composing scripts
                 wrote section by section before the bulk path existed)
    single_exec  every note in one exec request (the protocol-1 path; MAGDA's
                 dispatcher coalesces it into one undo step after the rebuild)
    notes_op     serve protocol 2 {"op":"notes"} (one request, one save)

A variant stops growing once a run exceeds --budget seconds, so the "before"
numbers on an old binary stay bounded. Results go to
~/Music/ComposerBench/bench/notes-<timestamp>.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from composerbench.adapter.magda import MagdaCliAdapter
from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.mcp.server import discover_cli
from composerbench.model import Note

OUT = Path.home() / "Music" / "ComposerBench" / "bench"


def notes(count: int, length: float) -> list[Note]:
    return [Note(start=(i * 0.25) % length, duration=0.2, pitch=48 + i % 24, velocity=60 + i % 60)
            for i in range(count)]


async def run(sizes: list[int], budget: float) -> dict:
    cli = discover_cli()
    if cli is None:
        raise SystemExit("magda_cli not found (set COMPOSERBENCH_CLI)")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = OUT / f"notes-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    daw = MagdaServeAdapter(cli, work / "bench.mgd")
    await daw.initialize()
    t0 = time.perf_counter()
    feats = await daw.features()
    report = {"cli": str(cli), "features": feats, "boot_seconds": round(time.perf_counter() - t0, 2),
              "sizes": sizes, "budget_seconds": budget, "runs": []}
    print(f"engine protocol {feats['protocol']}  features={ {k: v for k, v in feats.items() if v is True} }")
    over_budget: set[str] = set()
    try:
        for n in sizes:
            length = max(16.0, float(-(-n * 0.25 // 4) * 4))   # room for every note, whole bars
            for variant in ("chunked", "single_exec", "notes_op"):
                if variant in over_budget or (variant == "notes_op" and not feats.get("bulkNotes")):
                    continue
                track = await daw.create_track(f"{variant}_{n}")
                await daw.load_device(track.id, "magda_polysynth")
                clip = await daw.create_clip(track.id, "part", 0.0, length)
                batch = notes(n, length)
                start = time.perf_counter()
                if variant == "chunked":
                    for i in range(0, n, 64):
                        await MagdaCliAdapter.add_notes(daw, track.id, clip.id, batch[i:i + 64])
                elif variant == "single_exec":
                    await MagdaCliAdapter.add_notes(daw, track.id, clip.id, batch)
                else:
                    await daw.set_notes(track.id, clip.id, batch)
                elapsed = time.perf_counter() - start
                written = len(await daw.get_notes(track.id))
                row = {"variant": variant, "notes": n, "seconds": round(elapsed, 3),
                       "notes_per_second": round(n / elapsed, 1) if elapsed else None,
                       "verified": written == n}
                report["runs"].append(row)
                print(f"{variant:12s} {n:6d} notes  {elapsed:8.2f} s  "
                      f"{row['notes_per_second'] or 0:9.1f} notes/s  {'ok' if row['verified'] else 'MISMATCH'}",
                      flush=True)
                await daw.delete_track(track.id)
                if elapsed > budget:
                    over_budget.add(variant)
    finally:
        await daw.shutdown()
        out = work / "report.json"
        out.write_text(json.dumps(report, indent=2))
        (OUT / "latest-notes.json").write_text(json.dumps(report, indent=2))
        print(f"-> {out}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sizes", nargs="*", type=int, default=[250, 1000, 2500, 5040])
    ap.add_argument("--budget", type=float, default=90.0,
                    help="stop growing a variant after a run slower than this (seconds)")
    args = ap.parse_args()
    asyncio.run(run(args.sizes, args.budget))


if __name__ == "__main__":
    main()
