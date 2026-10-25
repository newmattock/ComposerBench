"""GRIEF ENGINE — dark / somber / fast chiptune, composed through ComposerBench -> MAGDA.

Key: A harmonic minor (the G# over Am is the ache). Tempo 150 BPM, 40 bars (~64 s).
Sections: shadow (dread, sparse) -> pursuit (motion begins) -> descent (full sprint,
16th-note arps, loudest) -> ruins (collapse, near-silence).

Env COMPOSERBENCH_CHECK_ONLY=1 validates the symbolic energy arc offline (no MAGDA,
no audio) using the harness's own estimator, then exits.
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda import MagdaCliAdapter
from composerbench.agent.loop import Action, LoopBudget, RevisionLoop
from composerbench.analysis.audio import analyze_wav, normalize_wav
from composerbench.composition.motifs import MotifStore, transpose as xpose
from composerbench.composition.state import CompositionPlan, Intent, Section, SidecarStore
from composerbench.model import Note

CLI = Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = Path("/tmp/mb_grief")
FINAL_DIR = Path.home() / "Music" / "ComposerBench"

BPM = 150.0
TOTAL_BEATS = 160.0  # 40 bars

# ---------------------------------------------------------------- harmony ----
# 8-beat chord slots cycling Am -> Dm -> E -> F  (E major = harmonic-minor V)
CHORD_CYCLE = [
    # root(bass), pad-less; arp set ascending
    {"name": "Am", "root": 33, "arps": [57, 60, 64, 69, 72]},   # A1, A3 C4 E4 A4 C5
    {"name": "Dm", "root": 38, "arps": [50, 53, 57, 62, 65]},   # D2, D3 F3 A3 D4 F4
    {"name": "Emaj", "root": 40, "arps": [52, 56, 59, 64, 68]}, # E2, E3 G#3 B3 E4 G#4
    {"name": "Fmaj", "root": 41, "arps": [53, 57, 60, 65, 69]}, # F2, F3 A3 C4 F4 A4
]

SECTIONS = {  # name -> (start_beat, end_beat)
    "shadow": (0.0, 32.0),
    "pursuit": (32.0, 64.0),
    "descent": (64.0, 128.0),
    "ruins": (128.0, 160.0),
}

def chord_at(slot_start: float) -> dict:
    return CHORD_CYCLE[int(slot_start // 8) % 4]

# ------------------------------------------------------------- velocities ----
SECTION_VEL = {"shadow": 0.82, "pursuit": 0.95, "descent": 1.08, "ruins": 0.60}

def vsec(section: str, base: int) -> int:
    return max(1, min(127, int(base * SECTION_VEL[section])))

def section_of(beat: float) -> str:
    for name, (a, b) in SECTIONS.items():
        if a <= beat < b:
            return name
    return "ruins"

# ------------------------------------------------------------ LEAD: lament ---
# 32-beat somber theme (relative beats). Descending sighs; G#4 (68) is the wound.
THEME = [
    (0.0, 1.5, 76), (1.5, 0.5, 74), (2.0, 2.0, 72),
    (4.0, 1.5, 71), (5.5, 0.5, 69), (6.0, 2.0, 68),
    (8.0, 1.5, 72), (9.5, 0.5, 71), (10.0, 2.0, 69),
    (12.0, 1.5, 68), (13.5, 0.5, 69), (14.0, 2.0, 64),
    (16.0, 1.5, 77), (17.5, 0.5, 76), (18.0, 2.0, 74),
    (20.0, 1.5, 72), (21.5, 0.5, 71), (22.0, 2.0, 69),
    (24.0, 2.0, 68), (26.0, 1.5, 64), (27.5, 0.5, 65),
    (28.0, 2.0, 64), (30.0, 2.0, 57),
]

def lead_notes():
    out = []
    # shadow: first half of the theme only, quiet statement
    for rel, dur, p in THEME[:12]:
        out.append(Note(start=rel, duration=dur * 0.95, pitch=p, velocity=vsec("shadow", 86)))
    # pursuit: full theme
    for rel, dur, p in THEME:
        out.append(Note(start=32.0 + rel, duration=dur * 0.95, pitch=p, velocity=vsec("pursuit", 94)))
    # descent: theme, then last 16 beats again +12 (the scream) — recorded as motif lineage
    for rel, dur, p in THEME:
        semi = 12 if rel >= 16.0 else 0
        out.append(Note(start=64.0 + rel, duration=dur * 0.95, pitch=p + semi,
                        velocity=vsec("descent", 102)))
    # ruins: the falling tail alone, exhausted
    for rel, dur, p in THEME[19:]:
        out.append(Note(start=128.0 + (rel - 24.0) * 1.4, duration=dur * 1.3,
                        pitch=p, velocity=vsec("ruins", 62)))
    return out

# ------------------------------------------------------------------- bass ----
def bass_notes():
    out = []
    for slot in range(0, int(TOTAL_BEATS // 8)):
        s = slot * 8.0
        sec = section_of(s)
        r = chord_at(s)["root"]
        if sec == "shadow":
            out.append(Note(start=s, duration=3.6, pitch=r, velocity=vsec(sec, 88)))
            out.append(Note(start=s + 4.0, duration=3.6, pitch=r + 12, velocity=vsec(sec, 80)))
        elif sec == "pursuit":
            for i in range(8):  # driving eighths, pop on beat 2 of the pair
                p = r + (12 if i in (2, 6) else 0)
                out.append(Note(start=s + i * 1.0, duration=0.42, pitch=p, velocity=vsec(sec, 104)))
        elif sec == "descent":
            for i in range(16):  # relentless 16ths w/ octave drive
                p = r + (12 if i % 8 in (3, 7) else 0)
                out.append(Note(start=s + i * 0.5, duration=0.22, pitch=p,
                                velocity=vsec(sec, 108 if i % 4 == 0 else 96)))
        else:  # ruins: one long dying root per slot
            out.append(Note(start=s, duration=7.4, pitch=r, velocity=vsec(sec, 58)))
    return out

# -------------------------------------------------------------------- arp ----
def arp_notes():
    out = []
    for slot in range(0, int(TOTAL_BEATS // 8)):
        s = slot * 8.0
        sec = section_of(s)
        tones = chord_at(s)["arps"]
        seq = tones + tones[-2:0:-1]  # up-down shimmer
        step = {"shadow": 1.0, "pursuit": 0.5, "descent": 0.25}.get(sec)
        if step is None:
            continue
        i = 0
        b = s
        while b < s + 7.75:
            out.append(Note(start=round(b, 3), duration=step * 0.42,
                            pitch=seq[i % len(seq)], velocity=vsec(sec, 82)))
            b += step
            i += 1
    return out

# ------------------------------------------------------------------ drums ----
KICK, SNARE, HAT = 36, 60, 96

def drum_notes():
    out = []
    for bar in range(int(TOTAL_BEATS // 4)):
        b = bar * 4.0
        sec = section_of(b)
        if sec == "shadow":  # heartbeat
            out.append(Note(start=b, duration=0.12, pitch=KICK, velocity=vsec(sec, 78)))
        elif sec == "pursuit":
            out.append(Note(start=b, duration=0.1, pitch=KICK, velocity=vsec(sec, 116)))
            out.append(Note(start=b + 2.0, duration=0.1, pitch=KICK, velocity=vsec(sec, 108)))
            out.append(Note(start=b + 1.0, duration=0.09, pitch=SNARE, velocity=vsec(sec, 104)))
            out.append(Note(start=b + 3.0, duration=0.09, pitch=SNARE, velocity=vsec(sec, 104)))
            for q in range(4):
                out.append(Note(start=b + q, duration=0.05, pitch=HAT, velocity=vsec(sec, 74)))
        elif sec == "descent":
            for off in (0.0, 1.5, 2.0, 3.5):
                out.append(Note(start=b + off, duration=0.1, pitch=KICK, velocity=vsec(sec, 122)))
            for off in (1.0, 3.0):
                out.append(Note(start=b + off, duration=0.09, pitch=SNARE, velocity=vsec(sec, 112)))
            for e in range(8):
                out.append(Note(start=b + e * 0.5, duration=0.04, pitch=HAT,
                                velocity=vsec(sec, 84 if e % 2 == 0 else 68)))
    return out

# ------------------------------------------------------------------ plan -----
PLAN = CompositionPlan(
    tempo_bpm=BPM,
    key="A harmonic minor",
    sections=[
        Section(name="shadow", bars=8, start_bar=1, target_energy=0.48),
        Section(name="pursuit", bars=8, start_bar=9, target_energy=0.64),
        Section(name="descent", bars=16, start_bar=17, target_energy=0.82),
        Section(name="ruins", bars=8, start_bar=33, target_energy=0.30),
    ],
)

# NOTE (verified by /tmp/probe_chip_devices.py): only "4osc" is an audible
# instrument over the MAGDA CLI — square/pulse/triangle/noise/etc load but
# render digital silence. Chip character comes from the writing, not the patch.
TRACKS = [
    ("lead", "4osc"), ("arp", "4osc"), ("bass", "4osc"), ("drums", "4osc"),
]

GENERATORS = {
    "lead": lead_notes,
    "arp": arp_notes,
    "bass": bass_notes,
    "drums": drum_notes,
}

# --------------------------------------------------------- offline pre-pass --
def check_only():
    """Validate the arc with the harness estimator BEFORE any MAGDA work."""
    from collections import defaultdict
    notes_by_track = {name: GENERATORS[name]() for name in GENERATORS}
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

# ------------------------------------------------------------------ main -----
def proposer_factory(motifs: MotifStore):
    stage = {"n": 0}

    def proposer(world):
        n = stage["n"]
        stage["n"] += 1
        if n == 0:
            # Record lineage: the lament states in pursuit, screams +12 in descent
            theme_pursuit = [nt for nt in lead_notes() if 32.0 <= nt.start < 64.0]
            theme_descent_hi = [nt for nt in lead_notes()
                                if 64.0 <= nt.start < 128.0 and nt.pitch >= 80]
            motifs.record("motif:lament", "lead", theme_pursuit[:6])
            motifs.record("motif:lament", "lead", theme_descent_hi[:6],
                          transformation="transpose", semitones=12)
            actions = [Action(type="set_tempo", bpm=BPM)]
            for name, device in TRACKS:
                actions += [
                    Action(type="create_track", name=name),
                    Action(type="load_device", track=name, name=device),
                    Action(type="create_clip", track=name, name=f"{name}-clip",
                           start_beats=0.0, length_beats=TOTAL_BEATS),
                    Action(type="add_notes", track=name, clip=f"{name}-clip",
                           notes=GENERATORS[name]()),
                ]
            return actions
        return []  # subsequent iterations: observe, validate, keep

    return proposer


async def main():
    if os.environ.get("COMPOSERBENCH_CHECK_ONLY"):
        raise SystemExit(0 if check_only() else 1)

    WORK.mkdir(parents=True, exist_ok=True)
    daw = MagdaCliAdapter(CLI, WORK / "proj.mgd")
    await daw.initialize()
    store = SidecarStore(WORK)
    store.write_intent(Intent(
        title="grief engine",
        goal="Dark somber fast chiptune: dread that accelerates into a desperate sprint, "
             "then collapses into ruins.",
        emotion={"overall": ["dark", "somber", "urgent"]},
        principles=["harmonic minor with raised 7th ache",
                    "relentless 16th-note arps carry the pace",
                    "energy arcs hard up then falls into near-silence"],
    ))
    store.write_plan(PLAN)

    motifs = MotifStore(WORK)
    loop = RevisionLoop(daw, PLAN, proposer_factory(motifs), WORK,
                        budget=LoopBudget(max_iterations=4), render_audio=True)
    history = await loop.run([
        "build the grief engine",
        "verify the arc holds under its own weight",
        "let the ruins settle",
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

    # Audibility gate BEFORE publishing: MIDI renders at -120 dBFS when no
    # instrument is loaded; refuse to ship silence.
    pre_stats = analyze_wav(path)
    if pre_stats.peak_dbfs <= -60.0:
        raise SystemExit(
            f"AUDIBILITY GATE FAILED: peak {pre_stats.peak_dbfs} dBFS — render is silence. "
            "Refusing to ship a silent file."
        )

    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    dest = FINAL_DIR / "grief_engine.wav"
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
    for line in MotifStore(WORK).lineage("motif:lament"):
        print("  ", line)


if __name__ == "__main__":
    asyncio.run(main())
