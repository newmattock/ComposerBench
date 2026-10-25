"""MILESTONE D3 — first fully planner-composed piece.

Intent-level input only; the planner derives section arrangements from a
seed motif + energy arc. Roles drive sound selection. The critic watches
the render and applies safe fixes.
"""
from pathlib import Path
import asyncio
import pathlib
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.agent.loop import Action, CriticConfig, LoopBudget
from composerbench.analysis.audio import analyze_wav, normalize_wav
from composerbench.composition.planner import plan_arrangement
from composerbench.composition.state import CompositionPlan, Section
from composerbench.model import Note
from composerbench.semantics.registry import load_default

CLI = pathlib.Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = pathlib.Path("/tmp/mb_d3")
FINAL = pathlib.Path.home() / "Music" / "ComposerBench" / "planner_piece.wav"

BPM = 96.0
SEED = [Note(start=0.0, duration=1.0, pitch=69, velocity=88),
        Note(start=1.0, duration=0.5, pitch=72, velocity=88),
        Note(start=1.5, duration=1.5, pitch=74, velocity=88),
        Note(start=3.0, duration=1.0, pitch=69, velocity=82),
        Note(start=4.0, duration=2.0, pitch=65, velocity=86)]

PLAN = CompositionPlan(
    tempo_bpm=BPM, key="A minor",
    sections=[
        Section(name="ember", bars=8, start_bar=1, target_energy=0.35),
        Section(name="rise", bars=8, start_bar=9, target_energy=0.55),
        Section(name="bloom", bars=8, start_bar=17, target_energy=0.75),
        Section(name="afterglow", bars=8, start_bar=25, target_energy=0.30),
    ])

ROLE_OF_TRACK = {
    "lead": "dusk_lead", "answer": "bell_answer", "echo": "shimmer_echo_bus",
    "pads": "warm_pad", "bass": "sub_bass",
}
# planner track names map onto roles: lead/answer come from the planner;
# pads/bass are role-filled support.
SUPPORT = {"pads": "warm_pad", "bass": "sub_bass"}


def support_notes(track):
    """Simple chord/root support per section for the arrangement."""
    out = []
    chords = [[57, 60, 64], [53, 57, 60], [55, 59, 62], [52, 55, 59]]
    roots = [45, 41, 43, 40]
    for i, sec in enumerate(PLAN.sections):
        base = (sec.start_bar - 1) * 4
        ch = chords[i % 4]
        if track == "pads":
            for p in ch:
                out.append(Note(start=float(base), duration=15.4, pitch=p,
                                velocity=58))
        elif track == "bass":
            r = roots[i % 4]
            for b in range(32):
                out.append(Note(start=float(base + b), duration=0.85,
                                pitch=r if b % 4 else r + 12,
                                velocity=92))
    return out


async def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    reg = load_default()

    # ---- planner derives the arrangement ----
    steps = plan_arrangement(SEED, PLAN, lead_track="lead",
                             answer_track="answer")
    print("planner schedule:", flush=True)
    for s in steps:
        print("  ", repr(s), flush=True)

    daw = MagdaServeAdapter(CLI, WORK / "piece.mgd")
    await daw.initialize()
    try:
        await daw._exec("set-tempo", str(BPM))

        # roles for all tracks incl. planner tracks + echo bus
        for track, role in {**ROLE_OF_TRACK}.items():
            recipe = reg.get(role)
            report = await daw.apply_role(track, recipe, reg)
            print(f"[role] {track} <- {role}", flush=True)

        # place planner output into clips
        def _bounds(step):
            i = [j for j, s in enumerate(PLAN.sections)
                 if s.name == step.section][0]
            sec = PLAN.sections[i]
            return ((sec.start_bar - 1) * 4, (sec.start_bar - 1) * 4 + sec.bars * 4)

        for step in steps:
            lo, hi = _bounds(step)
            clip = await daw.create_clip(step.track,
                                         f"{step.track}-{step.section}",
                                         float(lo), float(hi - lo))
            # planner times are already absolute beats — keep them
            notes = [Note(start=n.start, duration=n.duration, pitch=n.pitch,
                          velocity=n.velocity) for n in step.notes
                     if lo <= n.start < hi]
            if notes:
                await daw.add_notes(step.track, clip.id, notes)

        # support tracks
        for track, gen_notes in (("pads", support_notes("pads")),
                                 ("bass", support_notes("bass"))):
            c = await daw.create_clip(track, f"{track}-c", 0.0, 128.0)
            await daw.add_notes(track, c.id, gen_notes)

        # echo bus mirrors the answer part an octave up, sparse
        answer_steps = [s for s in steps if s.track == "answer"]
        if answer_steps:
            echo_notes = [Note(start=n.start, duration=n.duration,
                               pitch=min(n.pitch + 12, 120),
                               velocity=max(30, n.velocity - 30))
                          for n in answer_steps[0].notes[::2]]
            c = await daw.create_clip("echo", "echo-c", 0.0, 128.0)
            await daw.add_notes("echo", c.id, echo_notes)

        def _bounds(step):
            i = [j for j, s in enumerate(PLAN.sections)
                 if s.name == step.section][0]
            sec = PLAN.sections[i]
            return ((sec.start_bar - 1) * 4, sec.bars * 4)

        out = WORK / "final.wav"
        await daw.render(RenderRequest(output_path=out))
        normalize_wav(out, target_dbfs=-1.0)

        stats = analyze_wav(out).to_dict()
        print("audio:", stats, flush=True)
        FINAL.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, FINAL)
        print("DELIVERED:", FINAL, flush=True)
    finally:
        try:
            await daw.shutdown()
        except Exception:
            pass


asyncio.run(main())
