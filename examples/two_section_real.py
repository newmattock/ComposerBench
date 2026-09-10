"""Milestone 5/6 demo: RevisionLoop composes a real two-section piece in MAGDA.

Runs the actual snapshot -> edit -> validate -> render -> keep/revert cycle
against the headless CLI adapter. Every render is real Tracktion Engine audio.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from composerbench.adapter.magda import MagdaCliAdapter
from composerbench.agent.loop import Action, LoopBudget, RevisionLoop
from composerbench.analysis.audio import analyze_wav
from composerbench.composition.state import CompositionPlan, Section
from composerbench.model import Note

CLI = Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = Path("/tmp/mb_two_section")


def bass_line(start_beat: float, pitches, step=1.0, velocity=100):
    return [Note(start=start_beat + i * step, duration=step * 0.9, pitch=p, velocity=velocity)
            for i, p in enumerate(pitches)]


def proposer(world):
    notes_by_track = world["notes_by_track"]
    if not notes_by_track:
        # Goal 1: sparse intro (bars 1-4) + dense climax (bars 5-8)
        intro = [Note(start=i * 4.0, duration=3.0, pitch=p, velocity=70)
                 for i, p in enumerate([45, 48, 50, 52])]
        climax = bass_line(16.0, [33 + (i % 5) * 3 for i in range(16)], step=0.25, velocity=127)
        climax += [Note(start=16.0 + i * 0.25, duration=0.25, pitch=69 + (i % 12), velocity=120)
                   for i in range(16)]
        return [
            Action(type="create_track", name="bass"),
            Action(type="load_device", track="bass", name="4osc"),
            Action(type="create_clip", track="bass", name="intro",
                   start_beats=0.0, length_beats=16.0),
            Action(type="add_notes", track="bass", clip="intro", notes=intro),
            Action(type="create_clip", track="bass", name="climax",
                   start_beats=16.0, length_beats=16.0),
            Action(type="add_notes", track="bass", clip="climax", notes=climax),
        ]
    if "lift" in world.get("goal", ""):
        # Goal 2: lift the whole bass line — accepted only if constraints hold.
        return [Action(type="transpose_clip", track="bass", semitones=2)]
    return []


async def main():
    WORK.mkdir(parents=True, exist_ok=True)
    daw = MagdaCliAdapter(CLI, WORK / "proj.mgd")
    await daw.initialize()

    plan = CompositionPlan(
        sections=[Section(name="intro", bars=4, start_bar=1, target_energy=0.2),
                  Section(name="climax", bars=4, start_bar=5, target_energy=0.9)],
        tempo_bpm=110.0,
    )
    loop = RevisionLoop(daw, plan, proposer, WORK,
                        budget=LoopBudget(max_iterations=3), render_audio=True)
    history = await loop.run(["build two-section arc", "lift energy"])

    print("=" * 60)
    for rec in history:
        print(f"rev{rec.index}: {rec.decision.upper()} ({rec.duration_ms} ms)")
        print(f"  goal:   {rec.goal}")
        print(f"  reason: {rec.reason}")
        print(rec.diff_summary)
        for c in rec.constraint_results:
            print(f"  [{c['status']}] {c['constraint']} — {c['detail']}")
        if rec.render_path:
            stats = analyze_wav(Path(rec.render_path)).to_dict()
            print(f"  render: {Path(rec.render_path).name} "
                  f"peak={stats['peak_dbfs']}dBFS rms={stats['rms_dbfs']}dBFS")
        print("-" * 60)
    final = await daw.get_project()
    print(f"final project: tempo={final.tempo_bpm} tracks={final.tracks} "
          f"length={final.length_beats} beats")


if __name__ == "__main__":
    asyncio.run(main())
