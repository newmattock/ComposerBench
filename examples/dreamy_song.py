"""Upbeat + dreamy song, composed through the ComposerBench pipeline onto real MAGDA.

Intent -> plan -> RevisionLoop (snapshot/edit/validate/render/keep) ->
motif lineage recorded -> full render analyzed per section.

Harmony: Cmaj7 - Am7 - Fmaj7 - G (I-vi-IV-V, 8-beat chords, bright & floaty).
Tempo 122 BPM, 32 bars (~63 s).
"""

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda import MagdaCliAdapter
from composerbench.agent.loop import Action, LoopBudget, RevisionLoop
from composerbench.analysis.audio import analyze_wav
from composerbench.composition.motifs import MotifStore, transpose as xpose
from composerbench.composition.state import CompositionPlan, Section, SidecarStore
from composerbench.model import Note

CLI = Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = Path("/tmp/mb_dreamy")
FINAL_DIR = Path.home() / "Music" / "ComposerBench"

BPM = 122.0
TOTAL_BEATS = 128.0  # 32 bars

CHORDS = [  # (start_beat, pad voicing, arp tones ascending)
    (0.0,  [60, 64, 67, 71], [48, 60, 64, 67, 71, 72, 76, 79]),    # Cmaj7
    (32.0, [57, 60, 64, 67], [45, 57, 60, 64, 67, 69, 72, 76]),    # Am7
    (64.0, [53, 57, 60, 65], [41, 53, 57, 60, 65, 69, 72, 77]),    # Fmaj7
    (96.0, [55, 59, 62, 67], [43, 55, 59, 62, 67, 71, 74, 79]),    # G
]

MOTIF = [76, 79, 83, 81]  # E5 G5 B5 A5 — the "tiny dreamy melody"


def vel_for(section_beat: float, base: int) -> int:
    if section_beat >= 96:  # outro
        return int(base * 0.75)
    if section_beat >= 64:  # peak
        return min(127, int(base * 1.32))
    if section_beat >= 32:  # lift
        return int(base * 1.05)
    return base


def pads_notes():
    out = []
    for start, voicing, _ in CHORDS:
        v = vel_for(start, 74)
        for pitch in voicing:
            out.append(Note(start=start, duration=7.7, pitch=pitch, velocity=v))
    # final resolve: Cmaj7 held over the last 4 bars
    out += [Note(start=112.0, duration=15.5, pitch=p, velocity=70) for p in (60, 64, 67, 71)]
    return out


def bass_patterns():
    out = []
    # roots: C2, A1, F2, G2 — one slot per chord, eighths with octave pops
    schedule = [(0.0, 36), (32.0, 33), (64.0, 41), (96.0, 43)]
    for slot_start, root in schedule:
        is_intro = slot_start == 0.0
        is_outro = slot_start >= 96.0
        for b in range(64):                    # eighth notes across the 32-beat slot
            beat = slot_start + b * 0.5
            if is_intro and b % 8 != 0:        # intro: one soft root per bar only
                continue
            if is_outro and b % 16 != 0:       # outro: single long-feel roots
                continue
            octave_up = (b % 4 == 2)
            v = vel_for(beat, 96 if octave_up else 114)
            out.append(Note(start=beat, duration=0.42,
                            pitch=root + (12 if octave_up else 0), velocity=v))
    return out


def arp_notes():
    out = []
    for slot_start, _, tones in CHORDS:
        seq = tones + tones[-2:0:-1]          # up-down shimmer
        i = 0
        beat = slot_start + 2.0               # leave room for motif hook
        while beat < slot_start + 31.5:
            pitch = seq[i % len(seq)]
            v = vel_for(beat, 84)
            out.append(Note(start=round(beat, 3), duration=0.22, pitch=pitch, velocity=v))
            beat += 0.25
            i += 1
    return out


def motif_hook_notes(slot_start: float, semitones: int, vel: int) -> list[Note]:
    return [Note(start=slot_start + i * 0.5, duration=0.45,
                 pitch=m + semitones, velocity=vel)
           for i, m in enumerate(MOTIF)]


def pulse_notes():
    out = []
    for slot_start, _, _ in CHORDS[:3]:       # no pulse under outro
        for b in range(0, 32):
            beat = slot_start + b
            out.append(Note(start=beat, duration=0.08, pitch=36, velocity=127))
            out.append(Note(start=beat + 0.5, duration=0.06, pitch=91, velocity=88))
            if b % 2 == 1:
                out.append(Note(start=beat + 0.75, duration=0.05, pitch=93, velocity=70))
    return out


def proposer_factory(motifs: MotifStore):
    stage = {"n": 0}

    def proposer(world):
        n = stage["n"]
        stage["n"] += 1
        if n > 0:
            return []
        # One full-arrangement revision: the energy arc must exist before
        # the cross-section constraint can judge it.
        hooks = []
        for slot_start in (0.0, 32.0, 64.0, 96.0):
            semi = 12 if slot_start >= 64.0 else 0
            hooks += motif_hook_notes(slot_start, semi, vel_for(slot_start, 100))
        motifs.record("motif:dream-theme", "arp", hooks[0:4])
        motifs.record("motif:dream-theme", "arp", hooks[8:12],
                      transformation="transpose", semitones=12)
        return [
            Action(type="set_tempo", bpm=BPM),
            Action(type="create_track", name="pads"),
            Action(type="load_device", track="pads", name="4osc"),
            Action(type="create_clip", track="pads", name="bed",
                   start_beats=0.0, length_beats=TOTAL_BEATS),
            Action(type="add_notes", track="pads", clip="bed", notes=pads_notes()),
            Action(type="create_track", name="arp"),
            Action(type="load_device", track="arp", name="4osc"),
            Action(type="create_clip", track="arp", name="sparkle",
                   start_beats=0.0, length_beats=TOTAL_BEATS),
            Action(type="add_notes", track="arp", clip="sparkle",
                   notes=arp_notes() + hooks),
            Action(type="create_track", name="bass"),
            Action(type="load_device", track="bass", name="4osc"),
            Action(type="create_clip", track="bass", name="lowend",
                   start_beats=0.0, length_beats=TOTAL_BEATS),
            Action(type="add_notes", track="bass", clip="lowend", notes=bass_patterns()),
            Action(type="create_track", name="pulse"),
            Action(type="load_device", track="pulse", name="4osc"),
            Action(type="create_clip", track="pulse", name="groove",
                   start_beats=0.0, length_beats=TOTAL_BEATS),
            Action(type="add_notes", track="pulse", clip="groove", notes=pulse_notes()),
        ]

    return proposer


PLAN = CompositionPlan(
    tempo_bpm=BPM,
    key="C major",
    sections=[
        Section(name="intro", bars=8, start_bar=1, target_energy=0.35),
        Section(name="lift", bars=8, start_bar=9, target_energy=0.55),
        Section(name="peak", bars=8, start_bar=17, target_energy=0.85),
        Section(name="outro", bars=8, start_bar=25, target_energy=0.30),
    ],
)


async def main():
    WORK.mkdir(parents=True, exist_ok=True)
    daw = MagdaCliAdapter(CLI, WORK / "proj.mgd")
    await daw.initialize()
    store = SidecarStore(WORK)
    from composerbench.composition.state import Intent
    store.write_intent(Intent(
        title="upbeat dreamy",
        goal="Bright floating optimism: gentle maj7 washes over driving sparkle and pulse.",
        emotion={"overall": ["dreamy", "uplifting", "weightless"]},
        principles=["major 7th colors", "continuous 16th motion",
                    "energy arcs to peak then floats down"],
    ))
    store.write_plan(PLAN)

    motifs = MotifStore(WORK)
    loop = RevisionLoop(daw, PLAN, proposer_factory(motifs), WORK,
                        budget=LoopBudget(max_iterations=6), render_audio=True)
    history = await loop.run([
        "lay the harmonic bed",
        "weave the dreamy sparkle and low end",
        "bring the groove",
        "state the dream theme",
    ])
    print("=" * 62)
    for rec in history:
        print(f"rev{rec.index}: {rec.decision.upper()} — {rec.reason}")
        for c in rec.constraint_results:
            print(f"   [{c['status']}] {c['detail']}")
    print("=" * 62)

    final = WORK / "final.wav"
    path = await daw.render(RenderRequest(output_path=final))
    from composerbench.analysis.audio import normalize_wav
    normalize_wav(path, target_dbfs=-1.0)
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    dest = FINAL_DIR / "upbeat_dreamy.wav"
    shutil.copy2(path, dest)

    stats = analyze_wav(dest).to_dict()
    print(f"FINAL RENDER: {dest}")
    print("audio:", stats)

    notes_by_track = {}
    for t in await daw.get_tracks():
        notes_by_track[t.name] = await daw.get_notes(t.id)
    from composerbench.evaluation.constraints import section_energy
    print("section energies:")
    for s in PLAN.sections:
        e, st = section_energy(notes_by_track, PLAN, s.name)
        print(f"  {s.name:6s} E={e:.2f}  ({st.note_count} notes)")
    print("motif lineage:")
    for line in MotifStore(WORK).lineage("motif:dream-theme"):
        print("  ", line)


if __name__ == "__main__":
    asyncio.run(main())
