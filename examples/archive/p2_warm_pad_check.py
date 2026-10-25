"""Is the warm_pad fix audible? (item 4 follow-up)

Before the fix the warm_pad recipe set Surge XT's filter cutoff while the
filter itself stayed "Off", so the cutoff did nothing. This renders the same
chord three ways and measures brightness (spectral centroid) and level:

    A  old recipe   filter Off,     cutoff 0.38   (what shipped before)
    B  new recipe   the warm_pad role as it is now (LP 12 dB, cutoff 0.58)
    C  new filter   LP 12 dB,       cutoff 0.30   (does the cutoff act now?)

    .venv/bin/python examples/p2_warm_pad_check.py

Writes ~/Music/ComposerBench/p2check/<stamp>/report.json and latest.json.
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from composerbench.adapter.magda_serve import MagdaServeAdapter  # noqa: E402
from composerbench.model import Note  # noqa: E402

CLI = Path("~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli").expanduser()
OUT = Path("~/Music/ComposerBench/p2check").expanduser() / time.strftime("%Y%m%d-%H%M%S")
CHORD = (48, 52, 55, 59, 64)


def analyse(path: Path) -> dict:
    with wave.open(str(path), "rb") as w:
        raw, ch, width, rate = w.readframes(w.getnframes()), w.getnchannels(), w.getsampwidth(), w.getframerate()
    assert width == 2, width
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64).reshape(-1, ch).mean(axis=1) / 32768.0
    x = x[int(0.5 * rate):int(7.5 * rate)]                      # skip attack / release
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    freqs = np.fft.rfftfreq(x.size, 1.0 / rate)
    power = spec ** 2
    above = float(power[freqs > 2000].sum() / max(power.sum(), 1e-20))
    return {"centroid_hz": round(float((freqs * spec).sum() / max(spec.sum(), 1e-12)), 1),
            "rms_db": round(20 * math.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-12)), 2),
            "energy_above_2k": round(above, 4)}


async def variant(name: str, setup) -> dict:
    project = OUT / name / f"{name}.mgd"
    project.parent.mkdir(parents=True, exist_ok=True)
    daw = MagdaServeAdapter(CLI, project)
    await daw.initialize()
    try:
        await daw.set_project_tempo(120)
        info = await setup(daw)
        track = info["track"]
        clip = await daw.create_clip(track, "chord", 0, 16)
        await daw.set_notes(track, clip.id, [Note(start=0, duration=16, pitch=p, velocity=90) for p in CHORD])
        await daw.save_project()
        wav = OUT / f"{name}.wav"
        from composerbench.adapter.base import RenderRequest
        await daw.render(RenderRequest(output_path=wav, start_beats=0, length_beats=16, bit_depth=16))
        params = await daw.get_parameters(track, info["device"])
        shown = {m["name"]: {"value": round(m["value"], 4), "text": m.get("text")} for m in params.values()
                 if m["name"] in ("A Filter 1 Type", "A Filter 1 Cutoff")}
        return {**analyse(wav), "params": shown, "steps": info.get("steps"), "wav": str(wav)}
    finally:
        await daw.shutdown()


async def manual(daw: MagdaServeAdapter, filter_type: str, cutoff: float) -> dict:
    track = (await daw.create_track("pad")).id
    device = await daw.add_plugin(track, "Surge XT", as_instrument=True)
    await daw.set_parameter(track, device, "A Filter 1 Type", filter_type)
    await daw.set_parameter(track, device, "A Filter 1 Cutoff", cutoff)
    return {"track": track, "device": device}


async def role(daw: MagdaServeAdapter) -> dict:
    from composerbench.semantics.registry import load_default
    reg = load_default()
    result = await daw.apply_role("pad", reg.get("warm_pad"), reg)
    tracks = await daw.get_tracks()
    track = next(t.id for t in tracks if t.name == "pad")
    return {"track": track, "device": result["device"], "steps": result["steps"]}


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"A_old_filter_off": await variant("A", lambda d: manual(d, "Off", 0.38)),
              "B_warm_pad_role": await variant("B", role),
              "C_lp12_cutoff_030": await variant("C", lambda d: manual(d, "LP 12 dB", 0.30))}
    a, b, c = (report[k]["centroid_hz"] for k in ("A_old_filter_off", "B_warm_pad_role", "C_lp12_cutoff_030"))
    report["verdict"] = {
        "role_darker_than_old": b < a,
        "cutoff_now_acts": c < b,
        "centroid_drop_role_vs_old_pct": round(100 * (a - b) / a, 1) if a else None,
    }
    text = json.dumps(report, indent=1)
    (OUT / "report.json").write_text(text)
    (OUT.parent / "latest.json").write_text(text)
    print(text)
    return 0 if report["verdict"]["role_darker_than_old"] and report["verdict"]["cutoff_now_acts"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
