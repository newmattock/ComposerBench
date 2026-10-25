"""HYPERPOP session probe: what can this MAGDA build do *right now*?

Everything lands in ~/Music/ComposerBench/hyperpop_agent/probe/:
  probe.json        plugins, instrument + fx parameter maps, command probe,
                    sanity-render measurements, timings, tool availability
  *.wav             tiny 1-bar sanity renders (before/after parameter writes)

Nothing outside that folder is modified.
"""
import asyncio
import json
import os
import pathlib
import shutil
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from composerbench.adapter.base import RenderRequest  # noqa: E402
from composerbench.adapter.magda_serve import MagdaServeAdapter  # noqa: E402
from composerbench.analysis.audio import analyze_wav  # noqa: E402
from composerbench.model import Note  # noqa: E402
from composerbench.engine import discover_cli  # noqa: E402

CLI = discover_cli() or pathlib.Path("magda_cli")
OUT = pathlib.Path.home() / "Music" / "ComposerBench" / "hyperpop_agent" / "probe"
WORK = OUT / "proj"
REPORT = {"errors": []}
T0 = time.time()


def log(*a):
    print(f"[probe {time.time() - T0:6.1f}s]", *a, flush=True)


def save():
    (OUT / "probe.json").write_text(json.dumps(REPORT, indent=1, default=str))


def spectral(path):
    import numpy as np
    from composerbench.analysis.wav import read_wav
    x, sr = read_wav(path)
    m = x.mean(axis=1)
    if not len(m) or float(abs(m).max()) < 1e-7:
        return {"centroid_hz": 0.0, "width": 0.0}
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    f = np.fft.rfftfreq(len(m), 1 / sr)
    cen = float((f * spec).sum() / max(spec.sum(), 1e-12))
    width = 0.0
    if x.shape[1] == 2:
        mid = (x[:, 0] + x[:, 1]) / 2
        side = (x[:, 0] - x[:, 1]) / 2
        width = float(np.sqrt((side ** 2).mean()) / max(np.sqrt((mid ** 2).mean()), 1e-12))
    return {"centroid_hz": round(cen, 1), "width": round(width, 4)}


async def measure(daw, name, start, length):
    out = OUT / f"{name}.wav"
    t = time.time()
    await asyncio.wait_for(daw.render(RenderRequest(output_path=out, start_beats=start,
                                                    length_beats=length)), 240)
    st = analyze_wav(out).to_dict()
    st.update(spectral(out))
    st["render_seconds"] = round(time.time() - t, 2)
    log("render", name, st)
    return st


async def params_of(daw, tid):
    return [
        {"id": d.get("id"), "name": d.get("name"), "format": d.get("format"),
         "type": d.get("type"),
         "parameters": [{k: p.get(k) for k in ("index", "name", "value", "min", "max", "unit",
                                                "stableId", "choices")}
                        for p in d.get("parameters", [])]}
        for d in await daw.get_devices_full(tid)
    ]


def chord_notes(start, pitches, dur=3.5, vel=100):
    return [Note(start=start, duration=dur, pitch=p, velocity=vel) for p in pitches]


async def main():
    shutil.rmtree(OUT, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    REPORT["python"] = sys.version
    REPORT["cli"] = {"path": str(CLI), "exists": CLI.exists()}
    ff = shutil.which("ffmpeg") or next(
        (p for p in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg",
                     str(pathlib.Path.home() / ".homebrew/bin/ffmpeg")] if os.path.exists(p)), None)
    tools = {"ffmpeg": ff, "PATH": os.environ.get("PATH")}
    try:
        import matplotlib
        tools["matplotlib"] = matplotlib.__version__
    except Exception as e:  # noqa: BLE001
        tools["matplotlib"] = f"missing: {e}"
    import numpy
    tools["numpy"] = numpy.__version__
    REPORT["tools"] = tools
    save()

    daw = MagdaServeAdapter(CLI, WORK / "probe.mgd")
    t = time.time()
    await daw.initialize()
    await daw._ensure_session()
    REPORT["timing"] = {"boot_seconds": round(time.time() - t, 2)}
    log("engine up", REPORT["timing"])
    try:
        await daw._exec("set-tempo", "160")

        # ---------------------------------------------------- plugins
        try:
            REPORT["plugins"] = await daw.list_plugins()
            log("plugins:", [p.get("name") for p in REPORT["plugins"]])
        except Exception as e:  # noqa: BLE001
            REPORT["errors"].append(f"plugins: {e}")
        save()

        # ---------------------------------------------------- command probe
        cmd_probe = {}
        for cmd in (["help"], ["set-track-pan", "1", "-0.3"], ["set-track-mute", "1", "1"],
                    ["add-send", "1", "1"], ["add-automation-point", "1", "0", "0.5"],
                    ["set-master-volume", "0.9"], ["list-commands"]):
            try:
                reply = await daw._send_raw({"op": "exec", "commands": cmd})
                cmd_probe[" ".join(cmd)] = reply
            except Exception as e:  # noqa: BLE001
                cmd_probe[" ".join(cmd)] = f"EXC {type(e).__name__}: {e}"
                await daw._ensure_session()
        REPORT["command_probe"] = cmd_probe
        log("command probe done")
        save()

        # ---------------------------------------------------- instruments
        inst = {}
        for iid in ["4osc", "magda_polysynth", "magda_fm", "magda_kick", "magda_snare",
                    "magda_clap", "magda_hat", "magda_tom", "magda_bell", "magda_marimba"]:
            try:
                tr = await daw.create_track(f"i_{iid}")
                await daw.load_device(tr.id, iid)
                inst[iid] = await params_of(daw, tr.id)
            except Exception as e:  # noqa: BLE001
                inst[iid] = f"EXC {type(e).__name__}: {e}"
                REPORT["errors"].append(f"inst {iid}: {e}")
                await daw._ensure_session()
        REPORT["instruments"] = inst
        log("instrument maps:", {k: (len(v[0]["parameters"]) if isinstance(v, list) and v else v)
                                 for k, v in inst.items()})
        save()

        # ---------------------------------------------------- effects
        fx = {}
        candidates = ["magda_reverb", "magda_delay", "magda_chorus", "magda_eq", "magda_compressor",
                      "magda_distortion", "magda_filter", "magda_phaser", "magda_flanger",
                      "magda_limiter", "magda_bitcrusher", "magda_saturator", "magda_utility",
                      "magda_gate", "magda_tremolo", "magda_pitchshift", "magda_widener",
                      "magda_ott", "magda_overdrive", "magda_waveshaper", "magda_clipper",
                      "magda_autopan", "magda_lofi", "magda_sidechain",
                      "reverb", "delay", "chorus", "phaser", "compressor", "4bandEq",
                      "lowpass", "pitchShifter"]
        for fid in candidates:
            try:
                tr = await daw.create_track(f"fx_{fid}")
                before = {d.get("id") for d in await daw.get_devices_full(tr.id)}
                reply = await daw._send_raw({"op": "exec", "commands": ["add-fx", str(
                    await daw._numeric_track_id(tr.id)), fid]})
                after = await params_of(daw, tr.id)
                new = [d for d in after if d["id"] not in before]
                fx[fid] = {"reply": reply, "devices": new}
            except Exception as e:  # noqa: BLE001
                fx[fid] = f"EXC {type(e).__name__}: {e}"
                await daw._ensure_session()
        REPORT["fx"] = fx
        log("fx accepted:", [k for k, v in fx.items() if isinstance(v, dict) and v["devices"]])
        save()

        # ---------------------------------------------------- sanity renders
        tests = {}
        # A) 4osc: default vs "unison-ish" params (voices/detune/spread set high)
        tr = await daw.create_track("t_4osc")
        await daw.load_device(tr.id, "4osc")
        c = await daw.create_clip(tr.id, "t4", 0.0, 4.0)
        await daw.add_notes(tr.id, c.id, chord_notes(0.0, [60, 64, 67, 71]))
        tests["4osc_default"] = await measure(daw, "4osc_default", 0.0, 4.0)
        dev = (await daw.get_devices_full(tr.id))[0]
        changed = []
        for p in dev.get("parameters", []):
            n = str(p.get("name", "")).lower()
            if any(k in n for k in ("voices", "detune", "spread")):
                val = p.get("max", 1.0) if "voices" in n else (p.get("min", 0) + 0.6 * (p.get("max", 1) - p.get("min", 0)))
                try:
                    await daw.set_parameter(tr.id, dev["id"], p["name"], val)
                    changed.append((p["name"], val))
                except Exception as e:  # noqa: BLE001
                    changed.append((p["name"], f"EXC {e}"))
        tests["4osc_unison_changed"] = changed
        tests["4osc_unison"] = await measure(daw, "4osc_unison", 0.0, 4.0)
        save()

        # B) Surge XT: init patch vs filter enabled + unison
        try:
            surge = next(p for p in REPORT.get("plugins", []) if p.get("name") == "Surge XT")
            tr = await daw.create_track("t_surge")
            sdev = await daw.add_plugin(tr.id, surge["id"], as_instrument=True)
            await asyncio.sleep(4.0)
            c = await daw.create_clip(tr.id, "ts", 8.0, 4.0)
            await daw.add_notes(tr.id, c.id, chord_notes(8.0, [60, 64, 67, 71]))
            tests["surge_init"] = await measure(daw, "surge_init", 8.0, 4.0)
            params = await daw.external_parameters(sdev)
            pv = {p["name"]: p["value"] for p in params}
            tests["surge_before"] = {k: pv.get(k) for k in (
                "A Filter 1 Type", "A Filter 1 Cutoff", "A Osc 1 Unison Voices",
                "A Osc 1 Unison Detune", "A Osc 2 Mute", "A Osc 3 Mute", "A Filter Configuration")}
            # filter type index 1 (first real LP) -> try a few normalized values
            await daw.set_external_parameter(sdev, "A Filter 1 Type", 0.03)
            await daw.set_external_parameter(sdev, "A Filter 1 Cutoff", 0.15)
            await asyncio.sleep(0.5)
            tests["surge_lp"] = await measure(daw, "surge_lp", 8.0, 4.0)
            await daw.set_external_parameter(sdev, "A Filter 1 Cutoff", 0.9)
            await daw.set_external_parameter(sdev, "A Osc 1 Unison Voices", 0.45)
            await daw.set_external_parameter(sdev, "A Osc 1 Unison Detune", 0.3)
            await asyncio.sleep(0.5)
            tests["surge_unison_open"] = await measure(daw, "surge_unison_open", 8.0, 4.0)
            params = await daw.external_parameters(sdev)
            pv = {p["name"]: p["value"] for p in params}
            tests["surge_after"] = {k: pv.get(k) for k in tests["surge_before"]}
        except Exception as e:  # noqa: BLE001
            tests["surge"] = f"EXC {type(e).__name__}: {e}"
            REPORT["errors"].append(traceback.format_exc()[-1500:])
            await daw._ensure_session()
        save()

        # C) polysynth: 4 saws detuned (supersaw-ish) vs default
        tr = await daw.create_track("t_poly")
        await daw.load_device(tr.id, "magda_polysynth")
        c = await daw.create_clip(tr.id, "tp", 16.0, 4.0)
        await daw.add_notes(tr.id, c.id, chord_notes(16.0, [60, 64, 67, 71]))
        tests["poly_default"] = await measure(daw, "poly_default", 16.0, 4.0)
        dev = (await daw.get_devices_full(tr.id))[0]
        for name, val in [("Osc 2 Level", -6.0), ("Osc 3 Level", -6.0), ("Osc 4 Level", -6.0),
                          ("Osc 1 Fine", -14.0), ("Osc 2 Fine", 14.0), ("Osc 3 Fine", -6.0),
                          ("Osc 4 Fine", 7.0), ("Cutoff", 9000.0)]:
            await daw.set_parameter(tr.id, dev["id"], name, val)
        tests["poly_supersaw"] = await measure(daw, "poly_supersaw", 16.0, 4.0)
        REPORT["tests"] = tests
        save()

        # full project dump (schema reference)
        (OUT / "dump.json").write_text(json.dumps(await daw._dump_json(), indent=1)[:4_000_000])
        await daw.save_project()
    finally:
        await daw.shutdown()
        REPORT["elapsed"] = round(time.time() - T0, 1)
        save()
        log("done", REPORT["elapsed"], "s ->", OUT / "probe.json")


asyncio.run(main())
