"""AMBER HOUR — marimba & bell dusk-groove, composed through ComposerBench -> MAGDA.

Third song on the upgraded stack; showcases magda_marimba + magda_bell.
Key: D major. Tempo 110 BPM, 32 bars (~70 s).
Sections: dawn (solo marimba states the theme) -> bloom (band enters) ->
glow (bells echo the theme +12, fullest) -> drift (fade to embers).

COMPOSERBENCH_CHECK_ONLY=1 validates the symbolic energy arc offline.
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda import MagdaCliAdapter
from composerbench.agent.loop import Action, LoopBudget, RevisionLoop
from composerbench.analysis.audio import analyze_wav, normalize_wav
from composerbench.composition.motifs import MotifStore
from composerbench.composition.state import CompositionPlan, Intent, Section, SidecarStore
from composerbench.model import Note

CLI = Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = Path("/tmp/mb_amber")
FINAL_DIR = Path.home() / "Music" / "ComposerBench"

BPM = 110.0
TOTAL_BEATS = 128.0  # 32 bars, 16 chord slots of 8 beats

CHORDS = [
    # slot-relative harmony: D - Bm7 - Gmaj7 - A
    {"name": "D",     "root": 38,
     "pad": [50, 54, 57, 62],   "pool": [50, 57, 62, 66, 69],
     "theme": [(0.0, 1.0, 78), (1.0, 0.5, 81), (1.5, 1.5, 78),
               (3.5, 0.5, 74), (4.0, 2.0, 73)]},
    {"name": "Bm7",   "root": 35,
     "pad": [47, 50, 54, 57],   "pool": [47, 54, 59, 62, 66],
     "theme": [(0.0, 1.0, 74), (1.0, 0.5, 77), (1.5, 1.5, 74),
               (3.5, 0.5, 71), (4.0, 2.0, 69)]},
    {"name": "Gmaj7", "root": 43,
     "pad": [43, 50, 54, 59],   "pool": [43, 50, 55, 59, 62],
     "theme": [(0.0, 1.0, 74), (1.0, 0.5, 79), (1.5, 1.5, 78),
               (3.5, 0.5, 74), (4.0, 2.0, 71)]},
    {"name": "A",     "root": 45,
     "pad": [45, 49, 52, 57],   "pool": [45, 52, 57, 61, 64],
     "theme": [(0.0, 1.0, 76), (1.0, 0.5, 81), (1.5, 1.5, 78),
               (3.5, 0.5, 76), (4.0, 2.0, 77)]},
]

SECTIONS = {
    "dawn": (0.0, 32.0),
    "bloom": (32.0, 64.0),
    "glow": (64.0, 96.0),
    "drift": (96.0, 128.0),
}

INSTRUMENTS = {
    "marimba": "magda_marimba",
    "bells": "magda_bell",
    "pads": "magda_polysynth",
    "bass": "4osc",
    "kick": "magda_kick",
    "hats": "magda_hat",
}

VOLUMES = {
    "marimba": 1.00, "bells": 0.92, "pads": 0.82,
    "bass": 0.95, "kick": 1.05, "hats": 0.60,
}

SECTION_VEL = {"dawn": 0.90, "bloom": 1.00, "glow": 1.08, "drift": 0.55}

def vsec(section: str, base: int) -> int:
    return max(1, min(127, int(base * SECTION_VEL[section])))

def section_of(beat: float) -> str:
    for name, (a, b) in SECTIONS.items():
        if a <= beat < b:
            return name
    return "drift"

def chord_at(slot_start: float) -> dict:
    return CHORDS[int(slot_start // 8) % 4]

# ------------------------------------------------------------------ parts ----
def marimba_notes():
    """Theme states at each slot start; rolling pool fills the back half."""
    out = []
    for slot in range(0, int(TOTAL_BEATS // 8)):
        s = slot * 8.0
        sec = section_of(s)
        ch = chord_at(s)
        if sec == "drift":
            if slot % 4 == 0:  # slow remembrance: just the phrase head
                out.append(Note(start=s, duration=2.5, pitch=ch["theme"][0][2],
                                velocity=vsec(sec, 58)))
            continue
        step = 1.0 if sec == "dawn" else 0.5   # arp-fill subdivision
        for rel, dur, p in ch["theme"]:
            out.append(Note(start=s + rel, duration=dur * 0.92, pitch=p,
                            velocity=vsec(sec, 96)))
        seq = ch["pool"] + ch["pool"][-2:0:-1]
        i = 0
        b = s + 4.0
        while b < s + 7.75:
            out.append(Note(start=round(b, 3), duration=step * 0.42,
                            pitch=seq[i % len(seq)], velocity=vsec(sec, 78)))
            b += step
            i += 1
    return out


def bell_notes():
    """Glow: the theme echoed +12 on bells, thinned. Drift: one last ember."""
    out = []
    for slot in range(int(SECTIONS["glow"][0] // 8), int(SECTIONS["glow"][1] // 8)):
        s = slot * 8.0
        ch = chord_at(s)
        for rel, dur, p in ch["theme"][:4]:
            out.append(Note(start=s + rel * 1.5, duration=dur * 1.4,
                            pitch=p + 12, velocity=vsec("glow", 72)))
    out.append(Note(start=96.0, duration=4.0, pitch=86, velocity=52))   # ember 1
    out.append(Note(start=124.0, duration=3.5, pitch=81, velocity=44))  # ember 2
    return out


def pads_notes():
    out = []
    for slot in range(0, int(TOTAL_BEATS // 8)):
        s = slot * 8.0
        sec = section_of(s)
        if sec == "drift":
            continue
        for p in chord_at(s)["pad"]:
            out.append(Note(start=s, duration=7.6, pitch=p, velocity=vsec(sec, 62)))
    # final resolve: Dmaj held across the last 4 bars
    out += [Note(start=112.0, duration=15.4, pitch=p, velocity=56)
            for p in (50, 54, 57, 62)]
    return out


def bass_notes():
    out = []
    for slot in range(0, int(TOTAL_BEATS // 8)):
        s = slot * 8.0
        sec = section_of(s)
        r = chord_at(s)["root"]
        if sec == "dawn":
            out.append(Note(start=s, duration=7.4, pitch=r, velocity=vsec(sec, 74)))
        elif sec == "drift":
            out.append(Note(start=s, duration=7.4, pitch=r, velocity=vsec(sec, 52)))
        else:
            for i in range(8):
                p = r + (12 if i in (2, 6) else 0)
                out.append(Note(start=s + i * 1.0, duration=0.42, pitch=p,
                                velocity=vsec(sec, 100)))
    return out


def kick_notes():
    out = []
    for bar in range(int(TOTAL_BEATS // 4)):
        b = bar * 4.0
        sec = section_of(b)
        if sec == "bloom":
            outs = (0.0, 2.0)
        elif sec == "glow":
            outs = (0.0, 1.75, 2.0, 3.0)  # gentle syncopation
        else:
            continue
        for off in outs:
            out.append(Note(start=b + off, duration=0.1, pitch=36,
                            velocity=vsec(sec, 104)))
    return out


def hat_notes():
    out = []
    for bar in range(int(TOTAL_BEATS // 4)):
        b = bar * 4.0
        sec = section_of(b)
        if sec not in ("bloom", "glow"):
            continue
        for q in range(4):
            out.append(Note(start=b + q + 0.5, duration=0.05, pitch=42,
                            velocity=vsec(sec, 74)))
            if sec == "glow" and q % 2 == 1:
                out.append(Note(start=b + q + 0.75, duration=0.04, pitch=42,
                                velocity=vsec(sec, 58)))
    return out


GENERATORS = {
    "marimba": marimba_notes,
    "bells": bell_notes,
    "pads": pads_notes,
    "bass": bass_notes,
    "kick": kick_notes,
    "hats": hat_notes,
}

PLAN = CompositionPlan(
    tempo_bpm=BPM,
    key="D major",
    sections=[
        Section(name="dawn", bars=8, start_bar=1, target_energy=0.42),
        Section(name="bloom", bars=8, start_bar=9, target_energy=0.60),
        Section(name="glow", bars=8, start_bar=17, target_energy=0.80),
        Section(name="drift", bars=8, start_bar=25, target_energy=0.28),
    ],
)


def check_only():
    notes_by_track = {name: gen() for name, gen in GENERATORS.items()}
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
    print(f"total notes: {sum(len(v) for v in notes_by_track.values())}")
    print("ARC:", "OK" if ok else "BROKEN")
    return ok


def proposer_factory(motifs: MotifStore):
    stage = {"n": 0}

    def proposer(world):
        n = stage["n"]
        stage["n"] += 1
        if n == 0:
            # Lineage: dawn theme stated on marimba, echoed +12 on bells in glow
            dawn_theme = [Note(start=rel, duration=dur, pitch=p, velocity=90)
                          for rel, dur, p in CHORDS[0]["theme"]]
            glow_bells = [nt for nt in bell_notes() if 64.0 <= nt.start < 80.0]
            motifs.record("motif:amber-theme", "marimba", dawn_theme[:5])
            motifs.record("motif:amber-theme", "bells", glow_bells[:5],
                          transformation="transpose", semitones=12)
            actions = [Action(type="set_tempo", bpm=BPM)]
            for name, device in INSTRUMENTS.items():
                actions += [
                    Action(type="create_track", name=name),
                    Action(type="load_device", track=name, name=device),
                    Action(type="set_volume", track=name, volume=VOLUMES[name]),
                    Action(type="create_clip", track=name, name=f"{name}-clip",
                           start_beats=0.0, length_beats=TOTAL_BEATS),
                    Action(type="add_notes", track=name, clip=f"{name}-clip",
                           notes=GENERATORS[name]()),
                ]
            return actions
        return []

    return proposer


async def main():
    if os.environ.get("COMPOSERBENCH_CHECK_ONLY"):
        raise SystemExit(0 if check_only() else 1)

    WORK.mkdir(parents=True, exist_ok=True)
    daw = MagdaCliAdapter(CLI, WORK / "proj.mgd")
    await daw.initialize()
    store = SidecarStore(WORK)
    store.write_intent(Intent(
        title="amber hour",
        goal="Golden dusk warmth: rolling marimba theme answered by distant "
             "bells, soft polysynth glow, unhurried groove.",
        emotion={"overall": ["warm", "nostalgic", "serene"]},
        principles=["marimba leads, bells answer an octave up",
                    "energy rises with the band, fades to embers",
                    "no rush: 110 BPM dusk sway"],
    ))
    store.write_plan(PLAN)

    motifs = MotifStore(WORK)
    loop = RevisionLoop(daw, PLAN, proposer_factory(motifs), WORK,
                        budget=LoopBudget(max_iterations=3), render_audio=True)
    history = await loop.run([
        "paint the amber hour",
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

    pre_stats = analyze_wav(path)
    if pre_stats.peak_dbfs <= -60.0:
        raise SystemExit(
            f"AUDIBILITY GATE FAILED: peak {pre_stats.peak_dbfs} dBFS — render is silence."
        )

    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    dest = FINAL_DIR / "amber_hour.wav"
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
        print(f"  {s.name:8s} E={e:.2f}  ({st.note_count} notes)")
    print("motif lineage:")
    for line in MotifStore(WORK).lineage("motif:amber-theme"):
        print("  ", line)


if __name__ == "__main__":
    asyncio.run(main())
