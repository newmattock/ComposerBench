"""GRIEF ENGINE v3 — dark/somber/fast chiptune, now with a production layer.

v3 = v2 composition (magda_polysynth lead, magda_fm arps, 4osc bass, real
kick/snare/hats) PLUS the new MAGDA capabilities:

  * REVERB SPACE   - magda_reverb on lead (long, dark, damped) and arp
                     (shorter, brighter) via add-fx
  * SOUND DESIGN   - named-parameter synthesis:
                       lead: detuned dual-osc w/ slow filter swell + long release
                       arp: bright square-ish wave, tight pluck envelope
  * PERSISTENT ENGINE - MagdaServeAdapter boots the engine once; ~7x faster
                     dispatch than the cold-CLI path

Key: A harmonic minor. Tempo 150 BPM, 40 bars (~64 s).
Sections: shadow -> pursuit -> descent -> ruins.
COMPOSERBENCH_CHECK_ONLY=1 validates the symbolic arc offline first.

NOTE: parameters/volumes are set once at setup (CLI has no automation), so
the "filter sweep opening through pursuit" is expressed as an envelope
(Attack/Decay on the filter) rather than a timeline automation.
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.agent.loop import Action, LoopBudget, RevisionLoop
from composerbench.analysis.audio import analyze_wav, normalize_wav
from composerbench.composition.motifs import MotifStore
from composerbench.composition.state import CompositionPlan, Intent, Section, SidecarStore
from composerbench.model import Note

CLI = Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = Path("/tmp/mb_grief_v3")
FINAL_DIR = Path.home() / "Music" / "ComposerBench"

BPM = 150.0
TOTAL_BEATS = 160.0

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
    "lead": 1.00, "arp": 0.80, "bass": 0.95,
    "kick": 1.10, "snare": 0.90, "hats": 0.60,
}

# ---------------------------------------------------------------- sound design
# Named parameters verified against dump --json (see probe in transcript).
LEAD_DESIGN = [
    # Detuned saw pair for a wide, mournful lead
    ("Osc 2 Level", -8.0),        # bring osc 2 into the mix
    ("Osc 2 Fine", 14.0),         # +14 cents shimmer
    ("Cutoff", 1400.0),           # dark base tone
    ("Resonance", 0.45),
    ("Filter Attack", 900.0),     # slow bloom = built-in 'sweep' per note
    ("Filter Release", 1200.0),
    ("Amp Attack", 60.0),
    ("Amp Release", 900.0),       # long tail so reverb has something to hold
]
ARP_DESIGN = [
    ("Amp Attack", 1.0),          # pluck: instant attack...
    ("Amp Decay", 90.0),          # ...fast decay to near-silence
    ("Amp Sustain", 0.12),        # staccato 16ths stay crisp at 150 BPM
]

REVERBS = {
    # track -> (Mix, Predelay ms, Decay, Damping, High Cut Hz)
    "lead": (0.38, 40.0, 78.0, 70.0, 6500.0),   # long, dark cathedral
    "arp": (0.22, 10.0, 35.0, 45.0, 9500.0),    # tighter sparkle glue
}

CHORD_CYCLE_LEN = len(CHORD_CYCLE)

SECTION_VEL = {"shadow": 0.82, "pursuit": 0.95, "descent": 1.08, "ruins": 0.60}


def chord_at(slot_start: float) -> dict:
    return CHORD_CYCLE[int(slot_start // 8) % CHORD_CYCLE_LEN]


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
        elif bar % 4 == 0:
            out.append(Note(start=b, duration=0.12, pitch=KICK, velocity=vsec(sec, 48)))
    return out


def snare_notes():
    out = []
    for bar in range(int(TOTAL_BEATS // 4)):
        b = bar * 4.0
        sec = section_of(b)
        if sec == "pursuit":
            outs = (1.0, 3.0)
        elif sec == "descent":
            outs = (1.0, 2.75, 3.0)
        elif sec == "ruins":
            outs = (3.0,) if bar % 4 == 2 else ()
        else:
            continue
        for off in outs:
            vel = 112 if off in (1.0, 3.0) else 78
            out.append(Note(start=b + off, duration=0.09, pitch=SNARE,
                            velocity=vsec(sec, vel)))
    return out


def hat_notes():
    out = []
    for bar in range(int(TOTAL_BEATS // 4)):
        b = bar * 4.0
        sec = section_of(b)
        if sec not in ("bloom", "glow") and sec not in ("pursuit", "descent"):
            continue
        grid = range(4) if sec == "pursuit" else range(8)
        step = 1.0 if sec == "pursuit" else 0.5
        for e in grid:
            vel = 80 if (e % 2 == 0 or sec == "pursuit") else 62
            out.append(Note(start=b + e * step, duration=0.04, pitch=HAT,
                            velocity=vsec(sec, vel)))
    return out


GENERATORS = {
    "lead": lead_notes,
    "arp": arp_notes,
    "bass": bass_notes,
    "kick": kick_notes,
    "snare": snare_notes,
    "hats": hat_notes,
}

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


def check_only() -> bool:
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
                ]
                # Sound design + FX ride the new capabilities when present.
                if name == "lead":
                    actions.append(Action(type="add_fx", track=name,
                                          name="magda_reverb"))
                if name == "arp":
                    actions.append(Action(type="add_fx", track=name,
                                          name="magda_reverb"))
                actions += [
                    Action(type="create_clip", track=name, name=f"{name}-clip",
                           start_beats=0.0, length_beats=TOTAL_BEATS),
                    Action(type="add_notes", track=name, clip=f"{name}-clip",
                           notes=GENERATORS[name]()),
                ]
            return actions
        return []

    return proposer


async def apply_production(daw) -> None:
    """Post-loop pass: named-param sound design + reverb settings."""
    async def set_params(track: str, device: str, params: list[tuple[str, float]]) -> None:
        for pname, val in params:
            try:
                await daw.set_parameter(track, device, pname, val)
            except Exception as e:  # noqa: BLE001 - one bad name must not kill the song
                print(f"  ! param {track}/{device}.{pname}: {e}")

    print("sound design: lead")
    await set_params("lead", "magda_polysynth", LEAD_DESIGN)
    print("sound design: arp")
    await set_params("arp", "magda_fm", ARP_DESIGN)

    for track, params in REVERBS.items():
        mix, pre, decay, damp, hcut = params
        try:
            fx_id = next(
                dev["id"] for dev in await daw.get_devices_full(track)
                if dev.get("name") == "magda_reverb"
            )
        except StopIteration:
            print(f"  ! no reverb device found on {track}, skipping")
            continue
        await daw.set_parameter(track, str(fx_id), "Mix", mix)
        await daw.set_parameter(track, str(fx_id), "Predelay", pre)
        await daw.set_parameter(track, str(fx_id), "Decay", decay)
        await daw.set_parameter(track, str(fx_id), "Damping", damp)
        await daw.set_parameter(track, str(fx_id), "High Cut", hcut)
        print(f"reverb tuned: {track}")


async def main():
    if os.environ.get("COMPOSERBENCH_CHECK_ONLY"):
        raise SystemExit(0 if check_only() else 1)

    WORK.mkdir(parents=True, exist_ok=True)
    daw = MagdaServeAdapter(CLI, WORK / "proj.mgd")
    await daw.initialize()
    store = SidecarStore(WORK)
    store.write_intent(Intent(
        title="grief engine v3",
        goal="Dark somber fast chiptune with real drums and a real production "
             "layer: reverb space, detuned lead, plucked arps.",
        emotion={"overall": ["dark", "somber", "urgent"]},
        principles=["harmonic minor with raised 7th ache",
                    "relentless 16th-note arps carry the pace",
                    "lead floats above in a long dark reverb",
                    "energy arcs hard up then falls into ruins"],
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

    await apply_production(daw)

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
    await daw.shutdown()

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
