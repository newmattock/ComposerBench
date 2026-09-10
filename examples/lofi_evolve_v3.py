"""DUSTY CASSETTE v3.2 — lo-fi hip hop evolved over 100 critiqued loops.

Bug lineage:
- v2 died at loop 8: KEEP path skipped the snapshot revert, so clips stacked
  every improvement (+49 MB/loop) and mutations became inaudible.
- v3 restored a per-loop snapshot before building — but that snapshot itself
  carried the previous keep's clips, so growth continued (89->216->366s).
- v3.1 restored ONE pre-notes snapshot — and STILL grew, exposing the real
  root cause: MAGDA arranges same-track clips SEQUENTIALLY, and
  restore_snapshot does not purge clips at all. Three drum-part clips at
  position 0 made every render 3x too long (281s vs 94s), and the critic's
  section windows sampled stretched audio -> flat 49.x scores throughout.

v3.2 architecture — IMMUTABLE ARRANGEMENT:
- Exactly ONE clip per instrument track (kick/snare/hats merged into a single
  drum clip), created once at loop 1. Clips are never created again.
- Every later loop rewrites ONLY the notes inside those clips:
  delete_notes(all) -> verify empty via get_notes -> add_notes(new set).
  Accumulation is impossible by construction; renders stay 128 beats.
- Note counts are re-synced from LIVE project state each loop (never trusted
  from local counters), so any drift self-heals instead of corrupting.

v3.3 — REBUILD-BY-DELETE: v3.2's plan died on contact with reality:
adapter.delete_notes() raises UnsupportedOperation ("not-in-cli-dispatcher-
yet"). BUT the C++ dispatcher DOES ship delete-clip <clip-id> — the Python
wrapper just never exposed it. So each loop now: resolves current clip ids
from LIVE project state -> engine-level `delete-clip` on each ->
create_clip x4 -> add_notes. Clips are torn down and rebuilt every single
loop; nothing can accumulate because nothing survives a loop.

v3.4 — ANALYTIC MUTATIONS (the brain): flat ~49.6 scores showed random
bumps couldn't climb: a lost coin-flip resets to champion, so small arc
nudges never compound. Now the critic's MEASUREMENTS drive the genome:
- per-section level findings apply an analytic gain trim from the measured
  RMS error, applied on top of the current gain (compounds across loops)
- repeated arc failures escalate (+0.12, +0.24, +0.36 ...)
- centroid above the 350-1900 Hz warmth band darkens the mix directly:
  pad cutoff down, hat velocity down, melody register down
Random exploration continues at reduced weight for diversity.

v3.5 — REAL KNOBS (probe-driven instrumentation fix): a one-shot sensitivity
probe (probe_centroid_knobs.py) measured what actually moves the spectral
centroid. Results: the Surge XT pad IS the brightness (drums-only mix sits
at 122 Hz; with the pad ~3800 Hz) and its exposed "A Filter N Cutoff"
params are INERT (3778 vs 3801 Hz at 0.10 vs 0.85) — every pad_cutoff
mutation all night steered a disconnected knob. Hat density/velocity move
the centroid by single-digit Hz. Fix: chord pad swapped to internal
magda_polysynth whose named "Cutoff" (50-18000 Hz, verified param map +
grief_engine_v3 usage) genuinely filters, mapped log-scale from the genome;
chord reverb swapped to internal magda_reverb (parametric Mix + High Cut).
"""
from pathlib import Path
import asyncio
import base64
import json
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
WORK = pathlib.Path("/tmp/mb_lofi3")
FINAL = pathlib.Path.home() / "Music" / "ComposerBench" / "dusty_cassette.wav"
SFZ = pathlib.Path("~/Samples/AVL/BLACK_PEARL_2023_repack/Black_Pearl_2023_repack.sfz").expanduser()

BPM = 82.0
BOUNDS = [(0, 16), (16, 48), (48, 80), (80, 112), (112, 128)]
SECTION_NAMES = ["intro", "A", "B", "A2", "outro"]
TARGET_E = {"intro": 0.32, "A": 0.58, "B": 0.72, "A2": 0.62, "outro": 0.28}
INTENT = {"overall": ["warm", "nostalgic", "serene"]}
N_LOOPS = 100
PLATEAU = 30
RENDER_TIMEOUT_S = 300

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
        # ---- TONE GENES: frozen at probe-verified warm values (v3.6).
        # Evolution optimizes arrangement/energy; tone is a fixed design
        # decision, not a search dimension (see v3.6 note in header).
        # pad_cutoff .45 -> polysynth Cutoff ~= 415 Hz (chords sit 220-370 Hz
        # fundamentals: warm but fully present).
        "pad_cutoff": 0.45, "room_wet": 0.30,
        "drum_level": 0.95, "bass_level": 0.85, "echo_level": 0.28,
        "section_gain": {n: 1.0 for n in SECTION_NAMES},
    }


# Tone genes excluded from mutation entirely — the search space is now
# arrangement + energy only.
TONE_GENES = {"pad_cutoff", "room_wet", "vel_hat", "hat_16ths",
              "melody_register"}


def clone(g):
    return json.loads(json.dumps(g))


def mutate(g, findings, meta=None, rng=None):
    """v3.4: measurement-driven. Analytic trims from the critic's numbers,
    plus reduced-weight random exploration for diversity."""
    g = clone(g)
    rng = rng or random.Random()

    def bump(k, lo, hi, d):
        g[k] = round(max(lo, min(hi, float(g[k]) + rng.uniform(-d, d))), 3)

    dims = {f["dimension"] for f in findings}

    # ---- analytic level trim: correct half the measured dB error ----
    if "level" in dims and meta and "rmss" in meta:
        for f in findings:
            if f["dimension"] != "level":
                continue
            sec = f.get("section")
            if sec not in g["section_gain"]:
                continue
            expected_db = -20.0 + 12.0 * TARGET_E[sec]
            delta = meta["rmss"][sec] - expected_db
            if abs(delta) > 1.0:
                correction = max(-0.20, min(0.20, -delta / 24.0))
                g["section_gain"][sec] = round(
                    max(0.55, min(1.45,
                        g["section_gain"][sec] + correction)), 3)

    # ---- arc: escalate pressure on repeat offenders ----
    if "arc" in dims:
        for f in findings:
            if f["dimension"] != "arc":
                continue
            sec = f["section"]
            idx = SECTION_NAMES.index(sec) if sec in SECTION_NAMES else None
            if idx is not None and idx > 0:
                prev = SECTION_NAMES[idx - 1]
                lift = 0.12 + 0.12 * (1 if severity_of(findings, "arc") >= 2
                                      else 0)
                g["section_gain"][sec] = round(
                    min(1.5, g["section_gain"].get(sec, 1.0) + lift), 3)
                g["section_gain"][prev] = round(
                    max(0.60, g["section_gain"].get(prev, 1.0) - 0.05), 3)

    # ---- warmth steering: tone is FROZEN (TONE_GENES); the mix cannot go
    # dark because every tone gene is pinned at probe-verified values.

    if "masking" in dims:
        g["echo_level"] = round(max(0.10, g["echo_level"] - 0.06), 3)

    # ---- exploration (reduced weight; tone genes excluded) ----
    bump("swing", 0.05, 0.28, 0.02)
    bump("vel_snare", 60, 105, 3)
    if rng.random() < 0.25:
        g["melody_density"] = round(max(0.8, min(2.8,
            g["melody_density"] + rng.choice([-0.2, 0.2]))), 2)
    return g


def severity_of(findings, dim):
    return sum(1 for f in findings if f["dimension"] == dim)


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


def build_parts(g):
    """Returns one note list PER TRACK (drum parts pre-merged). Deterministic:
    seeded from the genome hash, so identical genomes -> identical songs."""
    rng = random.Random(hash(json.dumps(g, sort_keys=True)) % (2 ** 31))
    kick, snare, hats, bass, chords, melody = [], [], [], [], [], []
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
                kick.append(Note(start=b + off, duration=0.2,
                                 pitch=36,
                                 velocity=_v(g["vel_kick"], gain, rng)))
            for off in (1.0, 3.0):
                snare.append(Note(start=b + off, duration=0.2,
                                  pitch=38,
                                  velocity=_v(g["vel_snare"], gain, rng)))
            for q in range(4):
                hats.append(Note(
                    start=round(b + q, 3), duration=0.08, pitch=42,
                    velocity=_v(g["vel_hat"], gain, rng, 4)))
                hats.append(Note(
                    start=round(b + q + 0.5 + g["swing"] * 0.5, 3),
                    duration=0.08, pitch=42,
                    velocity=_v(g["vel_hat"] * 0.8, gain, rng, 4)))
                if rng.random() < g["hat_16ths"]:
                    hats.append(Note(
                        start=round(b + q + 0.75, 3), duration=0.06, pitch=44,
                        velocity=_v(g["vel_hat"] * 0.6, gain, rng, 3)))

            nb = max(1, int(round(g["bass_density"])))
            for off in sorted(rng.sample([0.0, 1.5, 2.0, 3.0],
                                         min(nb, 4)))[:nb]:
                p = ch["root"] + (12 if rng.random() < 0.2 else 0)
                bass.append(Note(start=b + off, duration=0.9,
                                 pitch=p,
                                 velocity=_v(88, gain, rng)))
            for p in ch["pad"]:
                chords.append(Note(
                    start=b, duration=3.7, pitch=p,
                    velocity=_v(54, gain, rng, 3)))

        if name in ("intro", "outro") and bar % 2 == 0:
            continue
        count = max(1, int(round(g["melody_density"])))
        slots = sorted(rng.sample([0.0, 1.0 + g["swing"] * 0.5, 2.0,
                                   3.0 + g["swing"] * 0.5],
                                  min(count + 1, 4)))[:count]
        for i, off in enumerate(slots):
            pool = ch["pool"]
            pitch = pool[(bar + i) % len(pool)] + g["melody_register"]
            melody.append(Note(
                start=round(b + off, 3),
                duration=rng.choice([0.8, 1.4, 1.9]),
                pitch=max(40, min(120, pitch)),
                velocity=_v(74, gain, rng)))

    drums = sorted(kick + snare + hats, key=lambda n: n.start)
    return {"drums": drums, "bass": bass, "chords": chords, "melody": melody}


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
    meta = {"mean_centroid": round(mean_c, 1),
            "rmss": {n: r for n, r in zip(SECTION_NAMES, rmss)}}
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
            if internal:
                await daw.load_device(t.id, internal)
            elif role_slug:
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

        # v3.5: chords on magda_polysynth (REAL Cutoff in Hz) + internal
        # magda_reverb (parametric Mix/High Cut). Surge XT pad removed: its
        # exposed cutoff params are inert and it dominated the spectrum.
        await make("chords", internal="magda_polysynth")
        chord_rev = await daw.add_fx(tracks["chords"], "magda_reverb")
        fx["chords_reverb"] = chord_rev
        devices["chords"] = "magda_polysynth"
        await make("melody", "dusk_lead")
        await make("bass", "sub_bass")

        td = await daw.create_track("drums")
        tracks["drums"] = td.id
        plugs = {p["name"]: p for p in await daw.list_plugins()}
        sfizz_desc = next(plugs[n] for n in plugs if n.lower() == "sfizz")
        ddev = await daw.add_plugin(td.id, sfizz_desc["id"], as_instrument=True)
        devices["drums"] = ddev
        state_xml = ('<form><param name="file" value="%s"/>'
                     '<param name="volume" value="0"/></form>' % SFZ)
        await daw._request({"op": "set_ext_state", "deviceId": ddev,
                            "state_b64": base64.b64encode(
                                state_xml.encode()).decode()})
        await asyncio.sleep(2.5)
        print("[build] tracks + sfizz/Black Pearl ready", flush=True)

        # ---- v3.3 REBUILD-BY-DELETE: clips are torn down and rebuilt EVERY
        # loop via engine-level delete-clip (undocumented in the Python
        # wrapper, present in the C++ dispatcher). Nothing survives a loop,
        # so nothing can accumulate. Clip ids always come from LIVE state.
        async def rebuild_arrangement(genome):
            # 1) resolve + delete every existing clip on our tracks
            for part in ("drums", "bass", "chords", "melody"):
                existing = await daw.get_clips(tracks[part])
                for c in existing:
                    await daw._exec("delete-clip",
                                    str(c.id.removeprefix("clip:")))
            # 2) fresh clips
            fresh = {}
            for part in ("drums", "bass", "chords", "melody"):
                c = await daw.create_clip(tracks[part], part, 0.0, 128.0)
                fresh[part] = c.id
            # 3) notes into the fresh clips (batched exec per part)
            rng = random.Random(hash(json.dumps(genome, sort_keys=True))
                                % (2 ** 31))
            parts = build_parts(genome)
            clip_num = {pid: int(str(pid).removeprefix("clip:"))
                        for pid in fresh.values()}
            for part, notes in parts.items():
                cid = clip_num[fresh[part]]
                batch = []
                for n in notes:
                    if not (-1e-6 <= n.start < 128.0):
                        raise RuntimeError(f"{part}: note at {n.start} "
                                           "outside clip")
                    batch += ["add-midi-note", str(cid), f"{n.start:.6f}",
                              str(n.pitch), str(n.duration), str(n.velocity)]
                if batch:
                    await daw._exec(*batch)
            return fresh

        async def write_notes(genome):
            """Rebuild the whole arrangement; verify against live state."""
            await rebuild_arrangement(genome)
            expected = build_parts(genome)
            live_total = 0
            for part in ("drums", "bass", "chords", "melody"):
                got = await daw.get_notes(tracks[part])
                live_total += len(got)
            want_total = sum(len(expected[p])
                             for p in ("drums", "bass", "chords", "melody"))
            if live_total != want_total:
                raise RuntimeError(f"live notes {live_total} != expected "
                                   f"{want_total} — arrangement drift")

            if "chords" in devices:
                # REAL cutoff: log-mapped genome 0.12-0.60 -> ~250-7000 Hz
                cut_hz = round(50.0 * (140.0 ** float(genome["pad_cutoff"])))
                await daw.set_parameter(tracks["chords"],
                                        devices["chords"], "Cutoff", cut_hz)
                # polysynth default osc mix is bright; keep osc1-only warmth
                for pname, val in (("Osc 2 Level", -60.0),
                                   ("Osc 3 Level", -60.0),
                                   ("Osc 4 Level", -60.0),
                                   ("Amp Attack", 40.0),
                                   ("Amp Release", 700.0),
                                   ("Vel Amp", 0.4)):
                    try:
                        await daw.set_parameter(tracks["chords"],
                                                devices["chords"],
                                                pname, val)
                    except Exception:
                        pass  # cosmetic; Cutoff is the load-bearing knob
            if "chords_reverb" in fx:
                rid = fx["chords_reverb"]
                await daw.set_parameter(tracks["chords"], str(rid),
                                        "Mix", float(genome["room_wet"]))
                # reverb return darkens WITH the pad (single tone gene):
                # pad_cutoff .34 -> ~3.1 kHz, .20 -> ~1.4 kHz
                hcut = max(1200.0, min(12000.0,
                          12000.0 * (float(genome["pad_cutoff"]) - 0.08)))
                await daw.set_parameter(tracks["chords"], str(rid),
                                        "High Cut", hcut)
            # melody synth: FIXED gentle lowpass (tone gene, frozen) —
            # 1600 Hz tames the dusk_lead's bright top without muting it
            try:
                await daw.set_parameter(tracks["melody"],
                                        devices.get("melody_device",
                                                    "magda_polysynth"),
                                        "Cutoff", 1600.0)
            except Exception:
                pass
            await daw.set_track_volume(tracks["melody"],
                                       genome["melody_level"])
            await daw.set_track_volume(tracks["drums"], genome["drum_level"])
            await daw.set_track_volume(tracks["bass"], genome["bass_level"])

        genome = base_genome()
        best_genome, best_score, best_wav = clone(genome), -1.0, None
        best_centroid = float("inf")
        history, plateau = [], 0
        for it in range(1, N_LOOPS + 1):
            async def attempt():
                await write_notes(genome)
                out = WORK / f"iter_{it:03d}.wav"
                await asyncio.wait_for(
                    daw.render(RenderRequest(output_path=out)),
                    RENDER_TIMEOUT_S)
                return out

            t0 = asyncio.get_event_loop().time()
            try:
                out = await attempt()
            except Exception as e:  # noqa: BLE001
                kind = type(e).__name__
                print(f"[{it:03d}] ERROR {kind}: {str(e)[:110]}",
                      flush=True)
                if isinstance(e, asyncio.TimeoutError):
                    print("FATAL: render exceeded watchdog — aborting.",
                          flush=True)
                    raise
                history.append({"iter": it, "error": f"{kind}: {str(e)[:160]}"})
                (WORK / "history.json").write_text(json.dumps(history, indent=1))
                continue

            stats_quick = analyze_wav(out)
            score, findings, meta = score_render(out)
            elapsed = asyncio.get_event_loop().time() - t0

            # structural guard: render MUST be the true song length
            expect_s = 128.0 / BPM * 60.0
            if abs(stats_quick.duration_seconds - expect_s) > 5:
                print(f"[{it:03d}] FATAL: render is "
                      f"{stats_quick.duration_seconds:.0f}s, expected "
                      f"{expect_s:.0f}s — arrangement leaked.", flush=True)
                raise SystemExit(3)

            # improvement = higher score; ON TIES or SMALL REGRESSIONS
            # (<=2 pts), a mix whose centroid sits closer to the warmth-band
            # centre still wins. Multi-step darkening must be able to compound
            # across loops even when a step temporarily dips the score.
            cand_c = meta["mean_centroid"]
            if best_wav is None:
                improved = True
            elif score > best_score:
                improved = True
            elif score >= best_score - 2.0:
                improved = abs(cand_c - 1125.0) < abs(best_centroid - 1125.0)
            else:
                improved = False
            if improved:
                best_score, best_genome, best_centroid = \
                    score, clone(genome), cand_c
                if best_wav is not None:
                    pathlib.Path(best_wav).unlink(missing_ok=True)
                best_wav = out
                plateau = 0
            else:
                plateau += 1
                out.unlink(missing_ok=True)

            dims = "; ".join(sorted({f['dimension'] for f in findings})) or "clean"
            print(f"[{it:03d}] score={score:6.2f} best={best_score:6.2f} "
                  f"{'*' if improved else ' '} [{dims}] "
                  f"c={meta['mean_centroid']:.0f}Hz {elapsed:4.1f}s", flush=True)
            entry = {"iter": it, "score": round(score, 2),
                     "best": round(best_score, 2), "improved": improved,
                     "secs": round(elapsed, 1),
                     "findings": [
                         {"dimension": f["dimension"],
                          "section": f["section"],
                          "severity": round(f["severity"], 2)}
                         for f in findings]}
            if improved:
                bg = best_genome
                entry["genome_at_best"] = {
                    "swing": bg["swing"], "vel_kick": bg["vel_kick"],
                    "vel_snare": bg["vel_snare"], "vel_hat": bg["vel_hat"],
                    "melody_density": bg["melody_density"],
                    "pad_cutoff": bg["pad_cutoff"],
                    "room_wet": bg["room_wet"],
                    "section_gain": bg["section_gain"]}
            history.append(entry)
            (WORK / "history.json").write_text(json.dumps(history, indent=1))

            # ---- advance the search: child of the champion, steered by the
            # critique of what we just heard
            genome = mutate(best_genome, findings, meta,
                            rng=random.Random(
                                (it * 2654435761)
                                ^ (hash(json.dumps(best_genome,
                                                   sort_keys=True))
                                   % (2 ** 31))))

            # ---- MONOTONIC TONE CONTROLLER (applied AFTER mutate, never
            # reverted): tone genes are FROZEN in v3.6 — this controller now
            # only guards against pathological brightness; it is a no-op
            # while the frozen design holds the mix inside the band.
            if meta and meta.get("mean_centroid", 0) > 3000:
                genome["pad_cutoff"] = round(
                    max(0.12, genome["pad_cutoff"] - 0.04), 3)
                genome = clone(genome)

            if plateau >= PLATEAU:
                print(f"plateau ({PLATEAU} loops) — done at {it}", flush=True)
                break

        # champion is already the live arrangement iff the last loop kept it;
        # rewrite explicitly to be certain, then final render
        await write_notes(best_genome)
        final = WORK / "final.wav"
        await asyncio.wait_for(daw.render(RenderRequest(output_path=final)),
                               RENDER_TIMEOUT_S)
        await daw.save_project()
        await daw.shutdown()

        normalize_wav(final, target_dbfs=-1.0)
        stats = analyze_wav(final)
        expect_s = 128.0 / BPM * 60.0
        if stats.peak_dbfs <= -60 or stats.rms_dbfs <= -30:
            raise SystemExit(f"final render bad: {stats.to_dict()}")
        if abs(stats.duration_seconds - expect_s) > 20:
            raise SystemExit(f"duration {stats.duration_seconds:.1f}s off "
                             f"expected {expect_s:.1f}s")
        FINAL.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final, FINAL)

        improvements = sum(1 for h in history if h.get("improved"))
        errors = sum(1 for h in history if "error" in h)
        report = {"song": "dusty_cassette",
                  "loops_run": len(history),
                  "start_score": history[0].get("score"),
                  "best_score": round(best_score, 2),
                  "improvements": improvements,
                  "errors_recovered": errors,
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
