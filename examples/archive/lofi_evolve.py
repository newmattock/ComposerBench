"""DUSTY CASSETTE v2 — lo-fi hip hop evolved over 100 critiqued loops.

v2 fixes vs v1: clip accumulation killed the first run (renders tripled in
length, genome changes inaudible). Now uses snapshot -> build -> render ->
score -> keep/revert-snapshot, exactly like RevisionLoop. Arc-directed
mutations now touch BOTH endpoints of a failing rise.
"""
from pathlib import Path
import asyncio
import json
import math
import pathlib
import random
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.analysis.audio import analyze_wav, normalize_wav
from composerbench.analysis.features import section_features
from composerbench.critique.critic import criticize
from composerbench.semantics.registry import load_default
from composerbench.model import Note

CLI = pathlib.Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = pathlib.Path("/tmp/mb_lofi2")
FINAL = pathlib.Path.home() / "Music" / "ComposerBench" / "dusty_cassette.wav"
SFZ = pathlib.Path("~/Samples/AVL/BLACK_PEARL_2023_repack/Black_Pearl_2023_repack.sfz").expanduser()

BPM = 82.0
BOUNDS = [(0, 16), (16, 48), (48, 80), (80, 112), (112, 128)]
SECTION_NAMES = ["intro", "A", "B", "A2", "outro"]
TARGET_E = {"intro": 0.32, "A": 0.58, "B": 0.72, "A2": 0.62, "outro": 0.28}
INTENT = {"overall": ["warm", "nostalgic", "serene"]}

CHORDS = [
    {"root": 45, "pad": [57, 60, 64], "pool": [69, 72, 76]},    # Am7
    {"root": 50, "pad": [62, 65, 69], "pool": [74, 77, 81]},    # Dm7
    {"root": 43, "pad": [59, 62, 66], "pool": [79, 83, 86]},    # Gmaj7
    {"root": 48, "pad": [60, 64, 67], "pool": [76, 79, 83]},    # Cmaj7
]


# ----------------------------------------------------------------- genome ----
def base_genome():
    return {
        "vel_kick": 96, "vel_snare": 84, "vel_hat": 62,
        "swing": 0.16, "hat_16ths": 0.35,
        "bass_density": 2.0, "melody_density": 1.6,
        "melody_register": 0, "melody_level": 0.78,
        "pad_cutoff": 0.34, "room_wet": 0.26,
        "drum_level": 0.95, "bass_level": 0.85, "echo_level": 0.28,
        "section_gain": {n: 1.0 for n in SECTION_NAMES},
    }


def clone(g):
    return json.loads(json.dumps(g))


def mutate(g, findings, rng):
    g = clone(g)

    def bump(k, lo, hi, d):
        g[k] = round(max(lo, min(hi, float(g[k]) + rng.uniform(-d, d))), 3)

    dims = {f["dimension"] for f in findings}
    if "arc" in dims:
        # failing rise: lift the later section AND ease the earlier one
        for f in findings:
            if f["dimension"] != "arc":
                continue
            sec = f["section"]
            idx = SECTION_NAMES.index(sec) if sec in SECTION_NAMES else None
            if idx is not None and idx > 0:
                prev = SECTION_NAMES[idx - 1]
                g["section_gain"][sec] = round(
                    min(1.4, g["section_gain"].get(sec, 1.0) + 0.12), 3)
                g["section_gain"][prev] = round(
                    max(0.65, g["section_gain"].get(prev, 1.0) - 0.06), 3)
    if "brightness" in dims:
        dark = any("too dark" in f.get("hint", "") for f in findings
                   if f["dimension"] == "brightness")
        if dark:
            g["pad_cutoff"] = round(min(0.6, g["pad_cutoff"] + 0.07), 3)
            g["vel_hat"] = min(90, g["vel_hat"] + 5)
        else:
            g["pad_cutoff"] = round(max(0.18, g["pad_cutoff"] - 0.07), 3)
            g["room_wet"] = round(max(0.12, g["room_wet"] - 0.04), 3)
            g["melody_register"] = max(-12, g["melody_register"] - 12)
    if "masking" in dims:
        g["echo_level"] = round(max(0.10, g["echo_level"] - 0.06), 3)
        g["vel_hat"] = max(40, g["vel_hat"] - 5)
    if "level" in dims:
        for f in findings:
            if f["dimension"] != "level":
                continue
            sec = f.get("section")
            if sec in g["section_gain"]:
                up = "louder" in f.get("hint", "")
                g["section_gain"][sec] = round(
                    max(0.6, min(1.4, g["section_gain"][sec]
                                 + (0.08 if up else -0.08))), 3)

    bump("swing", 0.05, 0.28, 0.03)
    bump("vel_snare", 60, 105, 4)
    bump("vel_hat", 40, 85, 4)
    if rng.random() < 0.35:
        g["melody_density"] = round(max(0.8, min(2.8,
            g["melody_density"] + rng.choice([-0.2, 0.2]))), 2)
    if rng.random() < 0.25:
        g["hat_16ths"] = round(max(0.0, min(0.7,
            g["hat_16ths"] + rng.choice([-0.1, 0.1]))), 2)
    if rng.random() < 0.2:
        g["melody_register"] = rng.choice([-12, 0, 0, 12])
    return g


# ------------------------------------------------------------- note builders --
def _gain(g, beat):
    for name, (a, z) in zip(SECTION_NAMES, BOUNDS):
        if a <= beat < z:
            return g["section_gain"].get(name, 1.0)
    return 1.0


def _sec(beat):
    return next(n for n, (a, z) in zip(SECTION_NAMES, BOUNDS) if a <= beat < z)


def _v(base, gain, rng, human=6):
    return max(20, min(127, int(base * gain + rng.randint(-human, human))))


def build_parts(g, rng):
    parts = {"kick": [], "snare": [], "hats": [], "bass": [],
             "chords": [], "melody": []}
    for bar in range(32):
        b = bar * 4.0
        name = _sec(b)
        gain = _gain(g, b)
        active = name != "intro" or bar % 4 == 3
        ch = CHORDS[bar % 4]

        if active:
            kicks = [0.0, 2.0] + ([2.75] if bar % 2 == 1 else [])
            if name == "B":
                kicks.append(3.5)
            for off in kicks:
                parts["kick"].append(Note(start=b + off, duration=0.2,
                                          pitch=36,
                                          velocity=_v(g["vel_kick"], gain, rng)))
            for off in (1.0, 3.0):
                parts["snare"].append(Note(start=b + off, duration=0.2,
                                           pitch=38,
                                           velocity=_v(g["vel_snare"], gain, rng)))
            for q in range(4):
                parts["hats"].append(Note(
                    start=round(b + q, 3), duration=0.08, pitch=42,
                    velocity=_v(g["vel_hat"], gain, rng, 4)))
                parts["hats"].append(Note(
                    start=round(b + q + 0.5 + g["swing"] * 0.5, 3),
                    duration=0.08, pitch=42,
                    velocity=_v(g["vel_hat"] * 0.8, gain, rng, 4)))
                if rng.random() < g["hat_16ths"]:
                    parts["hats"].append(Note(
                        start=round(b + q + 0.75, 3), duration=0.06, pitch=44,
                        velocity=_v(g["vel_hat"] * 0.6, gain, rng, 3)))

            nb = max(1, int(round(g["bass_density"])))
            for off in sorted(rng.sample([0.0, 1.5, 2.0, 3.0],
                                         min(nb, 4)))[:nb]:
                p = ch["root"] + (12 if rng.random() < 0.2 else 0)
                parts["bass"].append(Note(start=b + off, duration=0.9,
                                          pitch=p,
                                          velocity=_v(88, gain, rng)))
            for p in ch["pad"]:
                parts["chords"].append(Note(
                    start=b, duration=3.7, pitch=p,
                    velocity=_v(54 * (0.7 + 0.3 * g["pad_cutoff"]), gain,
                                rng, 3)))

        if name in ("intro", "outro") and bar % 2 == 0:
            continue
        count = max(1, int(round(g["melody_density"])))
        slots = sorted(rng.sample([0.0, 1.0 + g["swing"] * 0.5, 2.0,
                                   3.0 + g["swing"] * 0.5],
                                  min(count + 1, 4)))[:count]
        for i, off in enumerate(slots):
            pool = ch["pool"]
            pitch = pool[(bar + i) % len(pool)] + g["melody_register"]
            parts["melody"].append(Note(
                start=round(b + off, 3),
                duration=rng.choice([0.8, 1.4, 1.9]),
                pitch=max(40, min(120, pitch)),
                velocity=_v(74, gain, rng)))
    return parts


# ------------------------------------------------------------------ scoring ---
def score_render(path):
    stats = analyze_wav(path)
    if stats.peak_dbfs <= -60:
        return 0.0, [{"dimension": "silence", "section": "?", "severity": 1.0,
                      "hint": "silent render", "observed": 0, "expected": 0}], None
    feats = section_features(path, BOUNDS, BPM)
    rmss = [f["rms_dbfs"] for f in feats]
    findings = criticize(feats, SECTION_NAMES, INTENT, TARGET_E)

    score = 60.0
    for f in findings:
        score -= min(f["severity"], 1.0) * 15.0
    deltas = []
    for (a, b), name in zip(BOUNDS, SECTION_NAMES):
        expected = -20.0 + 12.0 * TARGET_E[name]
        got = rmss[SECTION_NAMES.index(name)]
        deltas.append(abs(got - expected))
    score += max(0.0, 22.0 - sum(deltas) * 2.0)
    mean_c = sum(f["centroid_hz"] for f in feats) / len(feats)
    if 350 <= mean_c <= 1900:
        score += 10.0
    width = sum(f["stereo_width"] for f in feats) / len(feats)
    if 0.05 <= width <= 0.45:
        score += 5.0
    meta = {"mean_centroid": round(mean_c, 1)}
    return max(0.0, min(100.0, score)), findings, meta


async def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    reg = load_default()
    daw = MagdaServeAdapter(CLI, WORK / "lofi.mgd")
    await daw.initialize()
    try:
        await daw._exec("set-tempo", str(BPM))

        tracks, devices, fx = {}, {}, {}

        async def make(name, role_slug=None, internal=None):
            t = await daw.create_track(name)
            tracks[name] = t.id
            if role_slug:
                recipe = reg.get(role_slug)
                pid = reg.resolve_engine(recipe.engine)
                if recipe.engine.startswith("magda_") or recipe.engine == "4osc":
                    await daw.load_device(t.id, pid)
                else:
                    devices[name] = await daw.add_plugin(
                        t.id, pid, as_instrument=recipe.as_instrument)
                    await asyncio.sleep(0.8)
                if recipe.space.reverb:
                    rid = await daw.add_fx(
                        t.id, reg.resolve_engine(recipe.space.reverb))
                    fx[f"{name}_reverb"] = rid
                    if recipe.space.reverb_wet is not None:
                        await daw.shape_external_param(
                            rid, ["wet level", "wet", "mix"],
                            recipe.space.reverb_wet)
                await daw.set_track_volume(t.id, recipe.level)
            elif internal:
                await daw.load_device(t.id, internal)

        await make("chords", "warm_pad")
        await make("melody", "dusk_lead")
        await make("bass", "sub_bass")

        td = await daw.create_track("drums")
        tracks["drums"] = td.id
        plugs = {p["name"]: p for p in await daw.list_plugins()}
        sfizz_desc = next(plugs[n] for n in plugs if n.lower() == "sfizz")
        ddev = await daw.add_plugin(td.id, sfizz_desc["id"], as_instrument=True)
        devices["drums"] = ddev
        import base64
        state_xml = ('<form><param name="file" value="%s"/>'
                     '<param name="volume" value="0"/></form>' % SFZ)
        await daw._request({"op": "set_ext_state", "deviceId": ddev,
                            "state_b64": base64.b64encode(
                                state_xml.encode()).decode()})
        await asyncio.sleep(2.0)
        print("[build] sfizz + Black Pearl ready", flush=True)

        PART_TRACK = {"kick": "drums", "snare": "drums", "hats": "drums",
                      "bass": "bass", "chords": "chords", "melody": "melody"}

        async def materialize(genome):
            """Snapshot-isolated rebuild: restore wipes prior clips."""
            rng = random.Random(hash(json.dumps(genome, sort_keys=True))
                                % (2 ** 31))
            parts = build_parts(genome, rng)
            for part_name, notes in parts.items():
                tid = tracks[PART_TRACK[part_name]]
                clip = await daw.create_clip(tid, part_name, 0.0, 128.0)
                if notes:
                    await daw.add_notes(tid, clip.id, notes)
            if "chords" in devices:
                await daw.shape_all_params(devices["chords"],
                                           ["cutoff"], genome["pad_cutoff"])
            if "chords_reverb" in fx:
                await daw.shape_external_param(
                    fx["chords_reverb"], ["wet level", "wet"],
                    genome["room_wet"])
            await daw.set_track_volume(tracks["melody"],
                                       genome["melody_level"])
            await daw.set_track_volume(tracks["drums"], genome["drum_level"])
            await daw.set_track_volume(tracks["bass"], genome["bass_level"])

        genome = base_genome()
        best_genome, best_score, best_wav = clone(genome), -1.0, None
        history, plateau = [], 0
        N = 100
        for it in range(1, N + 1):
            snap = await daw.snapshot(f"loop{it}")
            try:
                await materialize(genome)
                out = WORK / f"iter_{it:03d}.wav"
                await daw.render(RenderRequest(output_path=out))
                score, findings, meta = score_render(out)
            except Exception as e:  # noqa: BLE001
                print(f"[{it:03d}] ERROR {str(e)[:110]} — revert", flush=True)
                history.append({"iter": it, "error": str(e)[:160]})
                await daw.restore_snapshot(snap)
                genome = clone(best_genome)
                continue

            improved = score > best_score
            if improved:
                best_score, best_genome, best_wav = score, clone(genome), out
                plateau = 0
            else:
                plateau += 1
                await daw.restore_snapshot(snap)
                genome = clone(best_genome)

            dims = "; ".join(sorted({f['dimension'] for f in findings})) or "clean"
            print(f"[{it:03d}] score={score:6.2f} best={best_score:6.2f} "
                  f"{'*' if improved else ' '} [{dims}] "
                  f"c={meta['mean_centroid']:.0f}Hz", flush=True)
            history.append({"iter": it, "score": round(score, 2),
                            "best": round(best_score, 2), "improved": improved,
                            "findings": [
                                {"dimension": f["dimension"],
                                 "section": f["section"],
                                 "severity": round(f["severity"], 2)}
                                for f in findings]})
            (WORK / "history.json").write_text(json.dumps(history, indent=1))

            if plateau >= 30:
                print(f"plateau (30 loops) — done at {it}", flush=True)
                break

        # rebuild champion from clean restored state
        await materialize(best_genome)
        final = WORK / "final.wav"
        await daw.render(RenderRequest(output_path=final))
        await daw.save_project()
        await daw.shutdown()

        normalize_wav(final, target_dbfs=-1.0)
        stats = analyze_wav(final)
        if stats.peak_dbfs <= -60 or stats.duration_seconds > 140:
            raise SystemExit(f"final render bad: {stats.to_dict()}")
        FINAL.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final, FINAL)

        improvements = sum(1 for h in history if h.get("improved"))
        report = {"song": "dusty_cassette",
                  "loops_run": len(history),
                  "start_score": history[0].get("score"),
                  "best_score": round(best_score, 2),
                  "improvements": improvements,
                  "duration_s": round(stats.duration_seconds, 1),
                  "audio": stats.to_dict(),
                  "genome": best_genome}
        (FINAL.parent / "dusty_cassette_report.json").write_text(
            json.dumps(report, indent=1))
        print("=" * 60, flush=True)
        print(json.dumps(report, indent=1)[:800], flush=True)
        print("DELIVERED:", FINAL, flush=True)
    except Exception:
        try:
            await daw.shutdown()
        except Exception:
            pass
        raise


asyncio.run(main())
