"""AMBER HOUR v2 — same composition, upgraded sound engine.

Composition DNA preserved exactly from amber_hour.py (D major, 110 BPM,
32 bars, dawn->bloom->glow->drift, identical note generators so the offline
energy-arc check passes verbatim).

What v2 adds, using the open-sound-stack upgrades:
  - pads  : Surge XT (was magda_polysynth), warm-filter shaped by name
  - bass  : Surge XT (was 4osc), dark sub shaping by name
  - marimba : magda_marimba + Dragonfly Room Reverb (ensemble glue)
  - bells   : magda_bell + Dragonfly Hall Reverb (distant dusk space)
  - bell echo : ChowMatrix delay-network bus fed the glow theme (shimmer)
  - transport : MagdaServeAdapter (persistent engine, fast dispatch)

COMPOSERBENCH_CHECK_ONLY=1 validates the symbolic energy arc offline (unchanged).
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.analysis.audio import analyze_wav, normalize_wav
from composerbench.composition.motifs import MotifStore
from composerbench.composition.state import CompositionPlan, Intent, Section, SidecarStore
from composerbench.model import Note

CLI = Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = Path("/tmp/mb_amber_v2")
FINAL_DIR = Path.home() / "Music" / "ComposerBench"

BPM = 110.0
TOTAL_BEATS = 128.0  # 32 bars, 16 chord slots of 8 beats

CHORDS = [
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

# internal devices stay where they sing; externals take pads/bass
INTERNAL = {
    "marimba": "magda_marimba",
    "bells": "magda_bell",
    "bell_echo": "magda_bell",   # echo bus = second bell voice
    "kick": "magda_kick",
    "hats": "magda_hat",
}
EXTERNAL_INSTRUMENTS = {"pads", "bass"}          # -> Surge XT instances
SURGE_NAME = "Surge XT"
ROOM_NAME = "Dragonfly Room Reverb"
HALL_NAME = "Dragonfly Hall Reverb"
ECHO_NAME = "ChowMatrix"

VOLUMES = {
    "marimba": 1.00, "bells": 0.90, "bell_echo": 0.34,
    "pads": 0.58, "bass": 0.80,
    "kick": 1.05, "hats": 0.60,
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


def bell_echo_notes():
    """Sparse glow-only echoes for the ChowMatrix shimmer bus."""
    return [nt for nt in bell_notes() if nt.start < 96.0][::2]


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
    "bell_echo": bell_echo_notes,
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


async def shape_param(daw, device_id: int, want, value: float) -> bool:
    """Set the first parameter whose name contains any of `want`
    (case-insensitive). `want` may be a string or a list of candidates."""
    if isinstance(want, str):
        want = [want]
    try:
        params = await daw.external_parameters(device_id)
    except Exception:
        return False
    hit = next((p for p in params
                if any(w.lower() in p["name"].lower() for w in want)), None)
    if hit is None:
        return False
    await daw.set_external_parameter(device_id, hit["name"], value)
    return True


async def main():
    if os.environ.get("COMPOSERBENCH_CHECK_ONLY"):
        raise SystemExit(0 if check_only() else 1)

    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    classic = FINAL_DIR / "amber_hour.wav"
    if classic.exists():                      # preserve the original render
        shutil.copy2(classic, FINAL_DIR / "amber_hour_classic.wav")

    daw = MagdaServeAdapter(CLI, WORK / "proj.mgd")
    await daw.initialize()
    store = SidecarStore(WORK)
    store.write_intent(Intent(
        title="amber hour v2",
        goal="Golden dusk warmth, now in a real room: Surge-warm pads and sub "
             "bass under the marimba theme, bells answered across a hall, "
             "echo-shimmer trailing the glow.",
        emotion={"overall": ["warm", "nostalgic", "serene"]},
        principles=["marimba leads, bells answer an octave up",
                    "energy rises with the band, fades to embers",
                    "no rush: 110 BPM dusk sway"],
    ))
    store.write_plan(PLAN)

    await daw._exec("set-tempo", str(BPM))
    plugins = {p["name"]: p for p in await daw.list_plugins()}
    surge = plugins[SURGE_NAME]
    room = next((plugins[n] for n in plugins if ROOM_NAME.split()[1] in n
                 and "Room" in n), None)
    hall = next((plugins[n] for n in plugins if "Hall" in n
                 and "Dragonfly" in plugins[n]["name"]), None)
    echo = next((plugins[n] for n in plugins if "ChowMatrix" in n), None)

    track_ids = {}
    surge_devices = {}

    for name, gen in GENERATORS.items():
        t = await daw.create_track(name)
        track_ids[name] = t.id

        if name in EXTERNAL_INSTRUMENTS:
            dev = await daw.add_plugin(t.id, surge["id"], as_instrument=True)
            surge_devices[name] = dev
            await asyncio.sleep(1.0)           # let async VST3 init settle
        else:
            await daw.load_device(t.id, INTERNAL[name])

        clip = await daw.create_clip(t.id, f"{name}-clip", 0.0, TOTAL_BEATS)
        await daw.add_notes(t.id, clip.id, gen())
        await daw.set_track_volume(t.id, VOLUMES[name])
        print(f"[track] {name}: {'Surge#' + str(dev) if name in EXTERNAL_INSTRUMENTS else INTERNAL[name]}"
              f" ({len(GENERATORS[name]())} notes)", flush=True)

    # ---------------- sound design ----------------
    # Surge pads: close the filter a touch for dusk warmth
    if await shape_param(daw, surge_devices["pads"], "cutoff", 0.38):
        print("[design] pads filter warmed (cutoff 0.38)", flush=True)
    # Surge bass: darker, tighter
    await shape_param(daw, surge_devices["bass"], "cutoff", 0.30)

    # Dragonfly Room on marimba — light ensemble glue
    if room:
        rid = await daw.add_fx(track_ids["marimba"], room["id"])
        if await shape_param(daw, rid, ["wet level", "wet", "mix"], 0.30):
            print("[design] marimba room reverb wet=0.30", flush=True)
    # Dragonfly Hall on bells — distant dusk space
    if hall:
        hid = await daw.add_fx(track_ids["bells"], hall["id"])
        if await shape_param(daw, hid, ["wet level", "wet", "mix"], 0.55):
            print("[design] bells hall reverb wet=0.55", flush=True)
    # ChowMatrix on the bell-echo bus — default network = ambient echo
    if echo:
        eid = await daw.add_fx(track_ids["bell_echo"], echo["id"])
        print(f"[design] bell-echo bus running ChowMatrix (fx {eid})", flush=True)

    # ---------------- motif lineage (unchanged) ----------------
    motifs = MotifStore(WORK)
    dawn_theme = [Note(start=rel, duration=dur, pitch=p, velocity=90)
                  for rel, dur, p in CHORDS[0]["theme"]]
    glow_bells = [nt for nt in bell_notes() if 64.0 <= nt.start < 80.0]
    motifs.record("motif:amber-theme", "marimba", dawn_theme[:5])
    motifs.record("motif:amber-theme", "bells", glow_bells[:5],
                  transformation="transpose", semitones=12)

    # ---------------- render, gate, master ----------------
    final = WORK / "final.wav"
    path = await daw.render(RenderRequest(output_path=final))
    await daw.save_project()
    await daw.shutdown()

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
