"""Upbeat + dreamy song, v2 — rebuilt on the upgraded MAGDA/ComposerBench stack.

v2 upgrades over the original dreamy_song.py:
  * Real instruments instead of the single 4osc-on-every-track workaround:
      pads -> magda_polysynth   (warm poly synth washes)
      arp  -> magda_fm          (glassy FM shimmer)
      bass -> 4osc              (proven sub substrate)
      kick -> magda_kick        (REAL drum - the old 'pulse' track was silent!)
      hats -> magda_hat         (REAL hats - same story)
  * Actual mixing via the new set-track-volume CLI command.
  * COMPOSERBENCH_CHECK_ONLY=1 validates the symbolic energy arc offline.

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
from composerbench.analysis.audio import analyze_wav, normalize_wav
from composerbench.composition.motifs import MotifStore, transpose as xpose
from composerbench.composition.state import CompositionPlan, Intent, Section, SidecarStore
from composerbench.model import Note

CLI = Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = Path("/tmp/mb_dreamy_v2")
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

# Track -> instrument (all verified audible; see ~/.musicbench/instrument_manifest.json)
INSTRUMENTS = {
    "pads": "magda_polysynth",
    "arp": "magda_fm",
    "bass": "4osc",
    "kick": "magda_kick",
    "hats": "magda_hat",
}

# Mix levels via the new set-track-volume command (1.0 = unity)
VOLUMES = {"pads": 1.15, "arp": 0.90, "bass": 1.00, "kick": 1.05, "hats": 0.78}


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
    schedule = [(0.0, 36), (32.0, 33), (64.0, 41), (96.0, 43)]
    for slot_start, root in schedule:
        is_intro = slot_start == 0.0
        is_outro = slot_start >= 96.0
        for b in range(64):
            beat = slot_start + b * 0.5
            if is_intro and b % 8 != 0:
                continue
            if is_outro and b % 16 != 0:
                continue
            octave_up = (b % 4 == 2)
            v = vel_for(beat, 96 if octave_up else 114)
            out.append(Note(start=beat, duration=0.42,
                            pitch=root + (12 if octave_up else 0), velocity=v))
    return out


def arp_notes():
    out = []
    for slot_start, _, tones in CHORDS:
        seq = tones + tones[-2:0:-1]
        i = 0
        beat = slot_start + 2.0
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


def kick_notes():
    """Four-on-the-floor on real magda_kick (intro/outro drop out)."""
    out = []
    for slot_start, _, _ in CHORDS[:3]:
        for b in range(0, 32):
            out.append(Note(start=slot_start + b, duration=0.08,
                            pitch=36, velocity=min(127, vel_for(slot_start + b, 122))))
    return out


def hat_notes():
    """Offbeat eighths + 16th lift on odd beats (real magda_hat)."""
    out = []
    for slot_start, _, _ in CHORDS[:3]:
        for b in range(0, 32):
            beat = slot_start + b
            out.append(Note(start=beat + 0.5, duration=0.06, pitch=42,
                            velocity=vel_for(beat, 88)))
            if b % 2 == 1:
                out.append(Note(start=beat + 0.75, duration=0.05, pitch=42,
                                velocity=int(vel_for(beat, 70) * 0.8)))
    return out


def proposer_factory(motifs: MotifStore):
    stage = {"n": 0}

    def proposer(world):
        n = stage["n"]
        stage["n"] += 1
        if n > 0:
            return []
        hooks = []
        for slot_start in (0.0, 32.0, 64.0, 96.0):
            semi = 12 if slot_start >= 64.0 else 0
            hooks += motif_hook_notes(slot_start, semi, vel_for(slot_start, 100))
        motifs.record("motif:dream-theme", "arp", hooks[0:4])
        motifs.record("motif:dream-theme", "arp", hooks[8:12],
                      transformation="transpose", semitones=12)

        note_generators = {
            "pads": pads_notes,
            "arp": lambda: arp_notes() + hooks,
            "bass": bass_patterns,
            "kick": kick_notes,
            "hats": hat_notes,
        }
        actions = [Action(type="set_tempo", bpm=BPM)]
        for name, device in INSTRUMENTS.items():
            actions += [
                Action(type="create_track", name=name),
                Action(type="load_device", track=name, name=device),
                Action(type="set_volume", track=name, volume=VOLUMES[name]),
                Action(type="create_clip", track=name, name=name,
                       start_beats=0.0, length_beats=TOTAL_BEATS),
                Action(type="add_notes", track=name, clip=name,
                       notes=note_generators[name]()),
            ]
        return actions

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


def check_only():
    """Validate the arc with the harness estimator BEFORE any MAGDA work."""
    generators = {
        "pads": pads_notes,
        "arp": lambda: arp_notes(),
        "bass": bass_patterns,
        "kick": kick_notes,
        "hats": hat_notes,
    }
    notes_by_track = {name: gen() for name, gen in generators.items()}
    from composerbench.evaluation.constraints import section_energy
    est = {}
    print(f"{'section':8s} {'est_E':>6s} {'target':>7s} {'notes':>6s}")
    ok = True
    for s in PLAN.sections:
        e, st = section_energy(notes_by_track, PLAN, s.name)
        est[s.name] = e
        print(f"{s.name:8s} {e:6.2f} {s.target_energy:7.2f} {st.note_count:6d}")
    for prev, nxt in zip(PLAN.sections, PLAN.sections[1:]):
        if nxt.target_energy >= prev.target_energy:
            good = est[nxt.name] > est[prev.name]
            relation = f"{nxt.name}({est[nxt.name]:.2f}) > {prev.name}({est[prev.name]:.2f})"
        elif abs(nxt.target_energy - prev.target_energy) > 0.05:
            good = est[prev.name] > est[nxt.name]
            relation = f"{prev.name}({est[prev.name]:.2f}) > {nxt.name}({est[nxt.name]:.2f})"
        else:
            continue
        print(f"  [{'PASS' if good else 'FAIL'}] {relation}")
        ok &= good
    total = sum(len(v) for v in notes_by_track.values())
    print(f"total notes: {total}")
    print("ARC:", "OK" if ok else "BROKEN")
    return ok


async def main():
    import os
    if os.environ.get("COMPOSERBENCH_CHECK_ONLY"):
        raise SystemExit(0 if check_only() else 1)

    WORK.mkdir(parents=True, exist_ok=True)
    daw = MagdaCliAdapter(CLI, WORK / "proj.mgd")
    await daw.initialize()
    store = SidecarStore(WORK)
    store.write_intent(Intent(
        title="upbeat dreamy v2",
        goal="Bright floating optimism: gentle polysynth washes over glassy FM "
             "sparkle, real kick/hat groove, driving low end.",
        emotion={"overall": ["dreamy", "uplifting", "weightless"]},
        principles=["major 7th colors", "continuous 16th motion",
                    "energy arcs to peak then floats down"],
    ))
    store.write_plan(PLAN)

    motifs = MotifStore(WORK)
    loop = RevisionLoop(daw, PLAN, proposer_factory(motifs), WORK,
                        budget=LoopBudget(max_iterations=3), render_audio=True)
    history = await loop.run([
        "lay the harmonic bed and state the dream theme",
        "verify the arc holds under its own weight",
    ])
    print("=" * 62)
    for rec in history:
        print(f"rev{rec.index}: {rec.decision.upper()} — {rec.reason}")
        for c in rec.constraint_results:
            print(f"   [{c['status']}] {c['detail']}")
    print("=" * 62)

    final = WORK / "final.wav"
    path = await daw.render(RenderRequest(output_path=final))
    normalize_wav(path, target_dbfs=-1.0)

    # Audibility gate BEFORE publishing (silent render = broken arrangement).
    pre_stats = analyze_wav(path)
    if pre_stats.peak_dbfs <= -60.0:
        raise SystemExit(
            f"AUDIBILITY GATE FAILED: peak {pre_stats.peak_dbfs} dBFS — render is silence."
        )

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
