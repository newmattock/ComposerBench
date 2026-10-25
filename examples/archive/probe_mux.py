"""Decisive probe: full-timeline render with ONLY the two sfizz tracks
audible (synths muted via track volume 0.001). If drums/hiss show up here,
summing is fine and something else eats externals in full mixes."""
from pathlib import Path
import asyncio
import base64
import pathlib
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda_serve import MagdaServeAdapter
from composerbench.analysis.audio import analyze_wav

CLI = pathlib.Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
WORK = pathlib.Path("/tmp/mb_muxprobe")
DRUM_SFZ = pathlib.Path("~/Samples/AVL/BLACK_PEARL_2023_repack/Black_Pearl_2023_repack.sfz").expanduser()
TEXT_SFZ = pathlib.Path("~/Samples/AVL/DUSTY_TEXTURE/dusty_texture.sfz").expanduser()

from composerbench.model import Note


async def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    daw = MagdaServeAdapter(CLI, WORK / "mux.mgd")
    await daw.initialize()
    try:
        await daw._exec("set-tempo", "72")
        ids = {}
        devs = {}
        plugs = {p["name"]: p for p in await daw.list_plugins()}
        desc = next(plugs[n] for n in plugs if n.lower() == "sfizz")

        async def mk(name, sfz):
            t = await daw.create_track(name)
            ids[name] = t.id
            d = await daw.add_plugin(t.id, desc["id"], as_instrument=True)
            devs[name] = d
            state = ('<form><param name="file" value="%s"/>'
                     '<param name="volume" value="0"/></form>' % sfz)
            await daw._request({"op": "set_ext_state", "deviceId": d,
                                "state_b64": base64.b64encode(
                                    state.encode()).decode()})
            await asyncio.sleep(3.0)

        await mk("drums", DRUM_SFZ)
        await mk("tex", TEXT_SFZ)

        # simple content: kick every bar + hiss bed
        c = await daw.create_clip(ids["drums"], "drums", 0.0, 32.0)
        cid = int(str(c.id).removeprefix("clip:"))
        batch = []
        for bar in range(8):
            batch += ["add-midi-note", str(cid), f"{bar * 4:.6f}", "36",
                      "0.2", "100"]
        await daw._exec(*batch)
        c2 = await daw.create_clip(ids["tex"], "tex", 0.0, 32.0)
        cid2 = int(str(c2.id).removeprefix("clip:"))
        await daw._exec("add-midi-note", str(cid2), "0.000000", "72", "32.0",
                        "90")

        # add three SYNTH tracks with loud content, then mute them
        for nm, eng in (("chords", "magda_polysynth"), ("melody",
                                                        "magda_polysynth"),
                        ("bass", "magda_fm")):
            t = await daw.create_track(nm)
            ids[nm] = t.id
            await daw.load_device(t.id, eng)
            cc = await daw.create_clip(t.id, nm, 0.0, 32.0)
            ccid = int(str(cc.id).removeprefix("clip:"))
            await daw._exec("add-midi-note", str(ccid), "0.000000", "60",
                            "3.5", "110")
            await daw.set_track_volume(t.id, 0.001)

        out = WORK / "sfizz_only_full.wav"
        await asyncio.wait_for(
            daw.render(RenderRequest(output_path=out)), 300)
        s = analyze_wav(out)
        print(f"FULL-TIMELINE, synths muted: peak {s.peak_dbfs:.1f} dBFS "
              f"rms {s.rms_dbfs:.1f} dur {s.duration_seconds:.1f}s")

        # and the inverse: mute the sfizz tracks
        await daw.set_track_volume(ids["drums"], 0.001)
        await daw.set_track_volume(ids["tex"], 0.001)
        for nm in ("chords", "melody", "bass"):
            await daw.set_track_volume(ids[nm], 1.0)
        out2 = WORK / "synths_only_full.wav"
        await asyncio.wait_for(
            daw.render(RenderRequest(output_path=out2)), 300)
        s2 = analyze_wav(out2)
        print(f"FULL-TIMELINE, sfizz muted: peak {s2.peak_dbfs:.1f} dBFS "
              f"dur {s2.duration_seconds:.1f}s")
    finally:
        try:
            await daw.shutdown()
        except Exception:
            pass


asyncio.run(main())
