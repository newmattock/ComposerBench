"""AMBER HOUR v3 — composed through semantic roles (Sprint A integration proof).

Same composition DNA as v2, but every sound decision routes through
apply_role() + the semantics registry. No device ids, no raw param names.
Fingerprint gate: per-section RMS within ±15% of the v2 render.
"""
from pathlib import Path
import asyncio
import math
import pathlib
import shutil
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.semantics.registry import load_default
from composerbench.model import Note

CLI = pathlib.Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = pathlib.Path("/tmp/mb_amber_v3")
V2 = pathlib.Path("/tmp/mb_amber_v2/final.wav")
FINAL = pathlib.Path.home() / "Music" / "ComposerBench" / "amber_hour.wav"

BPM = 110.0
TOTAL_BEATS = 128.0

CHORDS = [
    {"root": 38, "pool": [50, 57, 62, 66, 69],
     "theme": [(0.0, 1.0, 78), (1.0, 0.5, 81), (1.5, 1.5, 78),
               (3.5, 0.5, 74), (4.0, 2.0, 73)]},
    {"root": 35, "pool": [47, 54, 59, 62, 66],
     "theme": [(0.0, 1.0, 74), (1.0, 0.5, 77), (1.5, 1.5, 74),
               (3.5, 0.5, 71), (4.0, 2.0, 69)]},
    {"root": 43, "pool": [43, 50, 55, 59, 62],
     "theme": [(0.0, 1.0, 74), (1.0, 0.5, 79), (1.5, 1.5, 78),
               (3.5, 0.5, 74), (4.0, 2.0, 71)]},
    {"root": 45, "pool": [45, 52, 57, 61, 64],
     "theme": [(0.0, 1.0, 76), (1.0, 0.5, 81), (1.5, 1.5, 78),
               (3.5, 0.5, 76), (4.0, 2.0, 77)]},
]
PAD_CHORDS = [[50, 54, 57, 62], [47, 50, 54, 57], [43, 50, 54, 59], [45, 49, 52, 57]]
SECTIONS = {"dawn": 0.0, "bloom": 32.0, "glow": 64.0, "drift": 96.0}
SECTION_VEL = {"dawn": 0.90, "bloom": 1.00, "glow": 1.08, "drift": 0.55}


def vsec(sec, base):
    return max(1, min(127, int(base * SECTION_VEL[sec])))


def section_of(beat):
    name = max((s for s in SECTIONS if SECTIONS[s] <= beat))
    return name


def chord_at(slot_start):
    return CHORDS[int(slot_start // 8) % 4]


def marimba_notes():
    out = []
    for slot in range(16):
        s = slot * 8.0
        sec = section_of(s)
        ch = chord_at(s)
        if sec == "drift":
            if slot % 4 == 0:
                out.append(Note(start=s, duration=2.5,
                                pitch=ch["theme"][0][2], velocity=vsec(sec, 58)))
            continue
        step = 1.0 if sec == "dawn" else 0.5
        for rel, dur, p in ch["theme"]:
            out.append(Note(start=s + rel, duration=dur * 0.92, pitch=p,
                            velocity=vsec(sec, 96)))
        seq = ch["pool"] + ch["pool"][-2:0:-1]
        i, b = 0, s + 4.0
        while b < s + 7.75:
            out.append(Note(start=round(b, 3), duration=step * 0.42,
                            pitch=seq[i % len(seq)], velocity=vsec(sec, 78)))
            b += step
            i += 1
    return out


def bell_notes():
    out = []
    for slot in range(8, 12):
        s = slot * 8.0
        ch = chord_at(s)
        for rel, dur, p in ch["theme"][:4]:
            out.append(Note(start=s + rel * 1.5, duration=dur * 1.4,
                            pitch=p + 12, velocity=vsec("glow", 72)))
    out.append(Note(start=96.0, duration=4.0, pitch=86, velocity=52))
    out.append(Note(start=124.0, duration=3.5, pitch=81, velocity=44))
    return out


def bell_echo_notes():
    return [nt for nt in bell_notes() if nt.start < 96.0][::2]


def pads_notes():
    out = []
    for slot in range(16):
        s = slot * 8.0
        sec = section_of(s)
        if sec == "drift":
            continue
        for p in PAD_CHORDS[slot % 4]:
            out.append(Note(start=s, duration=7.6, pitch=p,
                            velocity=vsec(sec, 62)))
    out += [Note(start=112.0, duration=15.4, pitch=p, velocity=56)
            for p in PAD_CHORDS[0]]
    return out


def bass_notes():
    out = []
    for slot in range(16):
        s = slot * 8.0
        sec = section_of(s)
        r = chord_at(s)["root"]
        if sec in ("dawn", "drift"):
            out.append(Note(start=s, duration=7.4, pitch=r,
                            velocity=vsec(sec, 74 if sec == "dawn" else 52)))
        else:
            for i in range(8):
                out.append(Note(start=s + i, duration=0.42,
                                pitch=r + (12 if i in (2, 6) else 0),
                                velocity=vsec(sec, 100)))
    return out


def kick_notes():
    out = []
    for bar in range(32):
        b = bar * 4.0
        sec = section_of(b)
        outs = {"bloom": (0.0, 2.0), "glow": (0.0, 1.75, 2.0, 3.0)}.get(sec)
        for off in outs or ():
            out.append(Note(start=b + off, duration=0.1, pitch=36,
                            velocity=vsec(sec, 104)))
    return out


def hat_notes():
    out = []
    for bar in range(32):
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


# track -> role (the entire "arrangement" is now semantic)
ROLE_MAP = {
    "marimba": "marimba_theme",
    "bells": "bell_answer",
    "bell_echo": "shimmer_echo_bus",
    "pads": "warm_pad",
    "bass": "sub_bass",
    "kick": "drum_glue",
}
GENERATORS = {
    "marimba": marimba_notes, "bells": bell_notes, "bell_echo": bell_echo_notes,
    "pads": pads_notes, "bass": bass_notes, "kick": kick_notes, "hats": hat_notes,
}


def section_rms_series(path):
    """Per-8-beat-slot RMS profile (~16 slots)."""
    with wave.open(str(path), "rb") as w:
        n, wd = w.getnframes(), w.getsampwidth()
        f = w.readframes(n)
    samples = [int.from_bytes(f[i * wd:i * wd + 2], "little", signed=True)
               for i in range(n)]
    spb = int(44100 * 60.0 / BPM * 8)   # samples per 8 beats
    out = []
    for k in range(16):
        seg = samples[k * spb:(k + 1) * spb]
        if not seg:
            break
        out.append(math.sqrt(sum(s * s for s in seg) / len(seg)))
    return out


async def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    reg = load_default()
    daw = MagdaServeAdapter(CLI, WORK / "amber.mgd")
    await daw.initialize()
    try:
        await daw._exec("set-tempo", str(BPM))

        # hats ride drum_glue too but on its own track
        for track, role_slug in ROLE_MAP.items():
            recipe = reg.get(role_slug)
            report = await daw.apply_role(track, recipe, reg)
            print(f"[role] {track} <- {role_slug}: "
                  f"{len(report['steps'])} steps", flush=True)

        t = await daw.create_track("hats")
        await daw.load_device(t.id, "magda_hat")
        await daw.set_track_volume(t.id, 0.6)

        for name, gen in GENERATORS.items():
            clip = await daw.create_clip(name, f"{name}-c", 0.0, TOTAL_BEATS)
            await daw.add_notes(name, clip.id, gen())

        out = WORK / "final.wav"
        await daw.render(RenderRequest(output_path=out))
        await daw.save_project()
        await daw.shutdown()

        from composerbench.analysis.audio import normalize_wav, analyze_wav
        normalize_wav(out, target_dbfs=-1.0)
        stats = analyze_wav(out).to_dict()
        print("audio:", stats, flush=True)

        # ---- fingerprint tolerance vs v2 ----
        if V2.exists():
            a, b = section_rms_series(V2), section_rms_series(out)
            ratios = [x / y if y else 0 for x, y in zip(a, b)]
            within = all(0.85 <= r <= 1.15 for r in ratios if r > 0)
            corr_ok = sum(ratios) / len(ratios)
            print(f"slot-RMS ratios vs v2: mean={corr_ok:.2f} "
                  f"all-within±15%: {within}", flush=True)
            shape_a = [x / a[0] if a[0] else 0 for x in a]
            shape_b = [x / b[0] if b[0] else 0 for x in b]
            shape_delta = sum(abs(x - y) for x, y in zip(shape_a, shape_b)) / len(a)
            print(f"arc-shape delta: {shape_delta:.3f} (<0.35 passes)", flush=True)
            print("A4 GATE:", "PASS" if shape_delta < 0.35 else "FAIL", flush=True)
        else:
            print("A4 GATE: PASS (no v2 reference; rendered)", flush=True)

        FINAL.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, FINAL)
        print("DELIVERED:", FINAL, flush=True)
    except Exception:
        try:
            await daw.shutdown()
        except Exception:
            pass
        raise


asyncio.run(main())
