"""GRIEF ENGINE v2 — dark / somber / fast chiptune, rebuilt on upgraded stack.

v2 upgrades over grief_engine.py:
  * Real instruments: lead -> magda_polysynth (cutting poly synth),
    arp -> magda_fm, bass -> 4osc.
  * REAL DRUMS: magda_kick / magda_snare / magda_hat. The v1 "drums" track
    used the fake 'noise' device and rendered total silence - the song had
    no percussion at all. v2 finally has the engine part of Grief Engine.
  * Actual mixing via set-track-volume.

Key: A harmonic minor. Tempo 150 BPM, 40 bars (~64 s).
Sections: shadow -> pursuit -> descent -> ruins.
COMPOSERBENCH_CHECK_ONLY=1 validates the arc offline first.
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
from composerbench.composition.motifs import MotifStore
from composerbench.composition.state import CompositionPlan, Intent, Section, SidecarStore
from composerbench.model import Note

CLI = Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = Path("/tmp/mb_grief_v2")
FINAL_DIR = Path.home() / "Music" / "ComposerBench"

BPM = 150.0
TOTAL_BEATS = 160.0  # 40 bars

CHORD_CYCLE = [
    {"name": "Am",   "root": 33, "arps": [57, 60, 64, 69, 72]},
    {"name": "Dm",   "root": 38, "arps": [50, 53, 57, 62, 65]},
    {"name": "Emaj", "root": 40, "arps": [52, 56, 59, 64, 68]},   # harmonic-minor V
    {"name": "Fmaj", "root": 41, "arps": [53, 57, 60, 65, 69]},
]

SECTIONS = {
    "shadow": (0.0, 32.0),
    "pursuit": (32.0, 64.0),
    "descent": (64.0, 128.0),
    "ruins": (128.0, 160.0),
}

INSTRUMENTS = {
    "lead": "magda_polysynth",
    "arp": "magda_fm",
    "bass": "4osc",
    "kick": "magda_kick",
    "snare": "magda_snare",
    "hats": "magda_hat",
}

VOLUMES = {
    "lead": 1.05, "arp": 0.85, "bass": 1.00,
    "kick": 1.10, "snare": 0.95, "hats": 0.65,
}


def chord_at(slot_start: float) -> dict:
    return CHORD_CYCLE[int(slot_start // 8) % 4]


SECTION_VEL = {"shadow": 0.82, "pursuit": 0.95, "descent": 1.08, "ruins": 0.60}

def vsec(section: str, base: int) -> int:
    return max(1, min(127, int(base * SECTION_VEL[section])))

def section_of(beat: float) -> str:
    for name, (a, b) in SECTIONS.items():
        if a <= beat < b:
            return name
    return "ruins"


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
    for rel, dur, p in THEME[:12]:
        out.append(Note(start=rel, duration=dur * 0.95, pitch=p, velocity=vsec("shadow", 88)))
    for rel, dur, p in THEME:
        out.append(Note(start=32.0 + rel, duration=dur * 0.95, pitch=p, velocity=vsec("pursuit", 96)))
    for rel, dur, p in THEME:
        semi = 12 if rel >= 16.0 else 0
        out.append(Note(start=64.0 + rel, duration=dur * 0.95, pitch=p + semi,
                        velocity=vsec("descent", 104)))
    for rel, dur, p in THEME[19:]:
        out.append(Note(start=128.0 + (rel - 24.0) * 1.4, duration=dur * 1.3,
                        pitch=p, velocity=vsec("ruins", 62)))
    return out


def bass_notes():
    out = []
    for slot in range(0, int(TOTAL_BEATS // 8)):
        s = slot * 8.0
        sec = section_of(s)
        r = chord_at(s)["root"]
        if sec == "shadow":
            out.append(Note(start=s, duration=3.6, pitch=r, velocity=vsec(sec, 90)))
            out.append(Note(start=s + 4.0, duration=3.6, pitch=r + 12, velocity=vsec(sec, 82)))
        elif sec == "pursuit":
            for i in range(8):
                p = r + (12 if i in (2, 6) else 0)
                out.append(Note(start=s + i * 1.0, duration=0.42, pitch=p, velocity=vsec(sec, 106)))
        elif sec == "descent":
            for i in range(16):
                p = r + (12 if i % 8 in (3, 7) else 0)
                out.append(Note(start=s + i * 0.5, duration=0.22, pitch=p,
                                velocity=vsec(sec, 110 if i % 4 == 0 else 98)))
        else:
            out.append(Note(start=s, duration=7.4, pitch=r, velocity=vsec(sec, 58)))
    return out


def arp_notes():
    out = []
    for slot in range(0, int(TOTAL_BEATS // 8)):
        s = slot * 8.0
        sec = section_of(s)
        tones = chord_at(s)["arps"]
        seq = tones + tones[-2:0:-1]
        step = {"shadow": 1.0, "pursuit": 0.5, "descent": 0.25}.get(sec)
        if step is None:
            continue
        i = 0
        b = s
        while b < s + 7.75:
            out.append(Note(start=round(b, 3), duration=step * 0.42,
                            pitch=seq[i % len(seq)], velocity=vsec(sec, 84)))
            b += step
            i += 1
    return out


KICK, SNARE, HAT = 36, 38, 42

def kick_notes():
    """Real magda_kick: heartbeat in shadow; full groove after."""
    out = []
    for bar in range(int(TOTAL_BEATS // 4)):
        b = bar * 4.0
        sec = section_of(b)
        if sec == "shadow":
            out.append(Note(start=b, duration=0.12, pitch=KICK, velocity=vsec(sec, 86)))
        elif sec == "pursuit":
            out.append(Note(start=b, duration=0.1, pitch=KICK, velocity=vsec(sec, 120)))
            out.append(Note(start=b + 2.0, duration=0.1, pitch=KICK, velocity=vsec(sec, 112)))
        elif sec == "descent":
            for off in (0.0, 1.5, 2.0, 3.5):
                out.append(Note(start=b + off, duration=0.1, pitch=KICK, velocity=vsec(sec, 126)))
        else:  # ruins: dying single hits
            if bar % 4 == 0:
                out.append(Note(start=b, duration=0.12, pitch=KICK, velocity=vsec(sec, 48)))
    return out


def snare_notes():
    """Real magda_snare: backbeat from pursuit onward."""
    out = []
    for bar in range(int(TOTAL_BEATS // 4)):
        b = bar * 4.0
        sec = section_of(b)
        if sec == "pursuit":
            outs = (1.0, 3.0)
        elif sec == "descent":
            outs = (1.0, 2.75, 3.0)          # extra ghost before beat 4
        elif sec == "ruins":
            outs = (3.0,) if bar % 4 == 2 else ()  # exhausted last gasps
        else:
            continue
        for off in outs:
            vel = 112 if off in (1.0, 3.0) else 78   # ghosts quieter
            out.append(Note(start=b + off, duration=0.09, pitch=SNARE,
                            velocity=vsec(sec, vel)))
    return out


def hat_notes():
    """Real magda_hat: quarters in pursuit, eighths in descent."""
    out = []
    for bar in range(int(TOTAL_BEATS // 4)):
        b = bar * 4.0
        sec = section_of(b)
        if sec == "shadow" or sec == "ruins":
            continue
        grid = range(4) if sec == "pursuit" else range(8)
        step = 1.0 if sec == "pursuit" else 0.5
        for e in grid:
            vel = 80 if (e % 2 == 0 or sec == "pursuit") else 62
            out.append(Note(start=b + e * step, duration=0.04, pitch=HAT,
                            velocity=vsec(sec, vel)))
    return out


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

GENERATORS = {
    "lead": lead_notes,
    "arp": arp_notes,
    "bass": bass_notes,
    "kick": kick_notes,
    "snare": snare_notes,
    "hats": hat_notes,
}


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
    total = sum(len(v) for v in notes_by_track.values())
    print(f"total notes: {total}")
    print("ARC:", "OK" if ok else "BROKEN")
    return ok


def proposer_factory(motifs: MotifStore):
    stage = {"n": 0}

    def proposer(world):
        n = stage["n"]
        stage["n"] += 1
        if n == 0:
            theme_pursuit = [nt for nt in lead_notes() if 32.0 <= nt.start < 64.0]
            theme_descent_hi = [nt for nt in lead_notes()
                                if 64.0 <= nt.start < 128.0 and nt.pitch >= 80]
            motifs.record("motif:lament", "lead", theme_pursuit[:6])
            motifs.record("motif:lament", "lead", theme_descent_hi[:6],
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
        title="grief engine v2",
        goal="Dark somber fast chiptune with real drums: dread that accelerates "
             "into a desperate sprint, then collapses into ruins.",
        emotion={"overall": ["dark", "somber", "urgent"]},
        principles=["harmonic minor with raised 7th ache",
                    "relentless 16th-note arps carry the pace",
                    "real drum engine drives the pursuit",
                    "energy arcs hard up then falls into near-silence"],
    ))
    store.write_plan(PLAN)

    motifs = MotifStore(WORK)
    loop = RevisionLoop(daw, PLAN, proposer_factory(motifs), WORK,
                        budget=LoopBudget(max_iterations=3), render_audio=True)
    history = await loop.run([
        "build the grief engine with its drum core",
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
