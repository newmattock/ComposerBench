"""One-shot knob-sensitivity probe: which genome knobs actually move the
spectral centroid? Builds a minimal project, renders A/B pairs, prints the
deltas. No evolution loop — pure measurement."""
from pathlib import Path
import asyncio
import base64
import json
import pathlib
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.analysis.audio import analyze_wav
from composerbench.analysis.features import section_features
from composerbench.semantics.registry import load_default

CLI = pathlib.Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = pathlib.Path("/tmp/mb_probe")

BPM = 82.0
SFZ = pathlib.Path("~/Samples/AVL/BLACK_PEARL_2023_repack/Black_Pearl_2023_repack.sfz").expanduser()
CHORDS = [
    {"root": 45, "pad": [57, 60, 64], "pool": [69, 72, 76]},
    {"root": 50, "pad": [62, 65, 69], "pool": [74, 77, 81]},
    {"root": 43, "pad": [59, 62, 66], "pool": [79, 83, 86]},
    {"root": 48, "pad": [60, 64, 67], "pool": [76, 79, 83]},
]


def build_parts(hat_16ths=0.35, vel_hat=62, melody_register=0,
                pad_vel_scale=1.0):
    kick, snare, hats, bass, chords, melody = [], [], [], [], [], []
    from composerbench.model import Note
    rng = random.Random(1234)
    for bar in range(32):
        b = bar * 4.0
        ch = CHORDS[bar % 4]
        for off in (0.0, 2.0):
            kick.append(Note(start=b + off, duration=0.2, pitch=36,
                             velocity=96))
        for off in (1.0, 3.0):
            snare.append(Note(start=b + off, duration=0.2, pitch=38,
                              velocity=84))
        for q in range(4):
            hats.append(Note(start=b + q, duration=0.08, pitch=42,
                             velocity=vel_hat))
            hats.append(Note(start=b + q + 0.58, duration=0.08, pitch=42,
                             velocity=int(vel_hat * 0.8)))
            if rng.random() < hat_16ths:
                hats.append(Note(start=b + q + 0.75, duration=0.06, pitch=44,
                                 velocity=int(vel_hat * 0.6)))
        nb = 2
        for off in sorted(rng.sample([0.0, 1.5, 2.0, 3.0], 2)):
            bass.append(Note(start=b + off, duration=0.9, pitch=ch["root"],
                             velocity=88))
        for p in ch["pad"]:
            chords.append(Note(start=b, duration=3.7, pitch=p,
                               velocity=max(20, int(54 * pad_vel_scale))))
        for i, off in enumerate((0.0, 2.0)):
            pool = ch["pool"]
            pitch = pool[(bar + i) % len(pool)] + melody_register
            melody.append(Note(start=b + off, duration=1.4,
                               pitch=max(40, min(120, pitch)), velocity=74))
    drums = sorted(kick + snare + hats, key=lambda n: n.start)
    return {"drums": drums, "bass": bass, "chords": chords,
            "melody": melody}


async def main():
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    reg = load_default()
    daw = MagdaServeAdapter(CLI, WORK / "probe.mgd")
    await daw.initialize()
    results = {}
    try:
        await daw._exec("set-tempo", str(BPM))
        tracks = {}
        for name in ("chords", "melody", "bass"):
            t = await daw.create_track(name)
            tracks[name] = t.id
        td = await daw.create_track("drums")
        tracks["drums"] = td.id
        plugs = {p["name"]: p for p in await daw.list_plugins()}
        sfizz_desc = next(plugs[n] for n in plugs if n.lower() == "sfizz")
        ddev = await daw.add_plugin(td.id, sfizz_desc["id"],
                                    as_instrument=True)
        state_xml = ('<form><param name="file" value="%s"/>'
                     '<param name="volume" value="0"/></form>' % SFZ)
        await daw._request({"op": "set_ext_state", "deviceId": ddev,
                            "state_b64": base64.b64encode(
                                state_xml.encode()).decode()})
        await asyncio.sleep(2.5)

        async def render_variant(tag, parts, cutoff=None):
            for part in ("drums", "bass", "chords", "melody"):
                existing = await daw.get_clips(tracks[part])
                for c in existing:
                    await daw._exec("delete-clip",
                                    str(c.id.removeprefix("clip:")))
                c = await daw.create_clip(tracks[part], part, 0.0, 128.0)
                cid = int(str(c.id).removeprefix("clip:"))
                batch = []
                for n in parts[part]:
                    batch += ["add-midi-note", str(cid), f"{n.start:.6f}",
                              str(n.pitch), str(n.duration), str(n.velocity)]
                if batch:
                    await daw._exec(*batch)
            out = WORK / f"v_{tag}.wav"
            await daw.render(RenderRequest(output_path=out))
            feats = section_features(out, [(0, 128)], BPM)
            c_hz = feats[0]["centroid_hz"]
            results[tag] = round(c_hz, 1)
            print(f"  {tag:22s} centroid={c_hz:7.1f} Hz", flush=True)
            return c_hz

        base = build_parts()
        print("BASELINE:", flush=True)
        await render_variant("base", build_parts())

        print("KNOB PROBES (delta vs base):", flush=True)
        await render_variant("hat16_zero", build_parts(hat_16ths=0.0))
        await render_variant("velhat_34", build_parts(vel_hat=34))
        await render_variant("melody_-12", build_parts(melody_register=-12))
        await render_variant("melody_-24", build_parts(melody_register=-24))

        # Surge XT cutoff probe via shape_all_params vs raw set_parameter
        recipe = reg.get("warm_pad")
        pid = reg.resolve_engine(recipe.engine)
        sdev = await daw.add_plugin(tracks["chords"], pid,
                                    as_instrument=recipe.as_instrument)
        await asyncio.sleep(1.0)
        params = await daw.external_parameters(sdev)
        names = [p["name"] for p in params]
        print(f"\nSurge XT exposed {len(names)} params; "
              f"cutoff-ish: {[n for n in names if 'cut' in n.lower()][:6]}",
              flush=True)
        await daw.shape_all_params(sdev, ["cutoff"], 0.10)
        await render_variant("cutoff_low_shape", base)
        await daw.shape_all_params(sdev, ["cutoff"], 0.85)
        await render_variant("cutoff_high_shape", base)

        print("\nSUMMARY:", flush=True)
        base_c = results.get("base", 0)
        for k, v in results.items():
            print(f"  {k:22s} {v:8.1f} Hz  ({v - base_c:+.1f})",
                  flush=True)
    finally:
        try:
            await daw.shutdown()
        except Exception:
            pass


asyncio.run(main())
