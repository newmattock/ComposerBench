"""Isolation probe: does the DUSTY_TEXTURE sfz make ANY sound via sfizz?
Renders the texture track SOLO (RenderRequest.track_ids)."""
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
WORK = pathlib.Path("/tmp/mb_texprobe")
SFZ = pathlib.Path("~/Samples/AVL/DUSTY_TEXTURE/dusty_texture.sfz").expanduser()
DRUM_SFZ = pathlib.Path("~/Samples/AVL/BLACK_PEARL_2023_repack/Black_Pearl_2023_repack.sfz").expanduser()

from composerbench.model import Note


async def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    daw = MagdaServeAdapter(CLI, WORK / "probe.mgd")
    await daw.initialize()
    try:
        await daw._exec("set-tempo", "72")
        t = await daw.create_track("tex")
        plugs = {p["name"]: p for p in await daw.list_plugins()}
        desc = next(plugs[n] for n in plugs if n.lower() == "sfizz")
        dev = await daw.add_plugin(t.id, desc["id"], as_instrument=True)
        state = ('<form><param name="file" value="%s"/>'
                 '<param name="volume" value="0"/></form>' % SFZ)
        await daw._request({"op": "set_ext_state", "deviceId": dev,
                            "state_b64": base64.b64encode(
                                state.encode()).decode()})
        await asyncio.sleep(4.0)

        c = await daw.create_clip(t.id, "tex", 0.0, 16.0)
        cid = int(str(c.id).removeprefix("clip:"))
        batch = []
        # one long hiss note + a few crackle hits
        batch += ["add-midi-note", str(cid), "0.000000", "72", "8.0", "100"]
        for i, k in enumerate((57, 58, 59)):
            batch += ["add-midi-note", str(cid), f"{2.0 + i:.6f}", str(k),
                      "0.5", "110"]
        await daw._exec(*batch)
        out = WORK / "tex_solo.wav"
        await daw.render(RenderRequest(output_path=out,
                                       track_ids=[t.id]))
        s = analyze_wav(out)
        print(f"TEXTURE SOLO: peak {s.peak_dbfs:.1f} dBFS "
              f"rms {s.rms_dbfs:.1f} dur {s.duration_seconds:.1f}s")

        # control: known-good drum sfz, single kick
        t2 = await daw.create_track("drums")
        dev2 = await daw.add_plugin(t2.id, desc["id"], as_instrument=True)
        state2 = ('<form><param name="file" value="%s"/>'
                  '<param name="volume" value="0"/></form>' % DRUM_SFZ)
        await daw._request({"op": "set_ext_state", "deviceId": dev2,
                            "state_b64": base64.b64encode(
                                state2.encode()).decode()})
        await asyncio.sleep(3.0)
        c2 = await daw.create_clip(t2.id, "d", 16.0, 16.0)
        cid2 = int(str(c2.id).removeprefix("clip:"))
        await daw._exec("add-midi-note", str(cid2), "0.000000", "36", "0.2",
                        "100")
        out2 = WORK / "kick_solo.wav"
        await daw.render(RenderRequest(output_path=out2,
                                       track_ids=[t2.id],
                                       start_beats=16.0, length_beats=16.0))
        s2 = analyze_wav(out2)
        print(f"KICK CONTROL: peak {s2.peak_dbfs:.1f} dBFS")
    finally:
        try:
            await daw.shutdown()
        except Exception:
            pass


asyncio.run(main())
