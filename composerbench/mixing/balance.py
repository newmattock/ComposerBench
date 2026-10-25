"""Balance pass for a built song: per-track loudness where each part leads,
compared with where it should sit, turned into fader moves.

    report = await balance_song("~/Music/ComposerBench/mysong", apply=True)

1. Every track is rendered on its own (MAGDA renders post-fader, so the
   renders add up to the mix exactly).
2. Each track's loudness is measured per section; its *level* is the median of
   the sections within 6 LU of its loudest one (where it plays a lead role,
   not where a tail spills over).
3. The target is ``reference_lufs + level`` (the track's ``level`` from the
   spec, else its preset's default level - see devices/levels.py).
4. The new fader is ``volume * 10 ** ((target - measured) / 20)``; when a fader
   would pass 2.0 (+6 dB), every target moves down together instead.

With ``apply`` the faders are written to the project and the stems (and
mix.wav) are rebuilt from the per-track renders with the new gains - exactly
what a re-render would give - so the mix stage can run again at once.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

import numpy as np

from composerbench.devices.levels import REFERENCE_LUFS, default_level
from composerbench.mixing.loudness import integrated_lufs


def track_level(sections: dict[str, float | None], window_lu: float = 6.0) -> float | None:
    """A part's loudness where it leads: median of the sections within ``window_lu`` of its loudest."""
    values = [v for v in sections.values() if v is not None and math.isfinite(v) and v > -70]
    if not values:
        return None
    top = max(values)
    return round(statistics.median(v for v in values if v >= top - window_lu), 2)


def measure_track(x: np.ndarray, sr: int, sections: list[tuple[str, float, float]]) -> dict:
    """Per-section integrated loudness of one track render (frames x channels)."""
    out = {}
    for name, a, b in sections:
        seg = x[int(a * sr):int(b * sr)]
        if len(seg) < int(0.4 * sr) or float(np.abs(seg).max(initial=0.0)) < 1e-4:
            continue
        v = integrated_lufs(seg, sr)
        out[name] = round(v, 1) if math.isfinite(v) else None
    return out


def plan(tracks: dict[str, dict], measured: dict[str, dict], reference_lufs: float = REFERENCE_LUFS,
         max_volume: float = 2.0) -> tuple[list[dict], float]:
    """One row per track (measured level, target, gain, new fader) and the reference shift.

    Relative balance is what matters (the master stage sets the final loudness), so when
    reaching the reference would push some fader past ``max_volume`` every target moves
    down together by the same amount instead of clamping that one track.
    """
    rows, raw = [], {}
    for name, info in tracks.items():
        m = measured.get(name)
        level = info.get("level")
        if level is None:
            level = default_level(info.get("preset"), info.get("role"))
        lvl = track_level(m["sections"]) if m else None
        row = {"track": name, "stem": info.get("stem"), "volume": info["volume"], "level": level,
               "measured_lufs": lvl, "sections": m["sections"] if m else {}}
        if level is None or lvl is None or info["volume"] <= 0:
            row.update(new_volume=info["volume"], gain_db=0.0,
                       note="no level for this part" if level is None else "silent")
        else:
            raw[name] = info["volume"] * 10 ** ((reference_lufs + level - lvl) / 20)
        rows.append(row)
    shift = min(0.0, 20 * math.log10(max_volume / max(raw.values()))) if raw else 0.0
    for row in rows:
        if row["track"] in raw:
            target = reference_lufs + shift + row["level"]
            gain = target - row["measured_lufs"]
            row.update(target_lufs=round(target, 2), gain_db=round(gain, 2),
                       new_volume=round(min(row["volume"] * 10 ** (gain / 20), max_volume), 4))
    return rows, round(shift, 2)


async def balance_song(song_dir: Path | str, cli_path: Path | str | None = None, apply: bool = False,
                       reference_lufs: float | None = None, levels: dict[str, float] | None = None,
                       progress=None) -> dict[str, Any]:
    """Measure every track of a song built by composerbench.song and balance its faders.

    ``levels`` overrides target levels per track (LU relative to the kick).
    """
    from composerbench.adapter.base import RenderRequest
    from composerbench.adapter.magda_serve import MagdaServeAdapter
    from composerbench.analysis.wav import read_wav
    from composerbench.devices.presets import PRESETS
    from composerbench.engine import require_cli
    from composerbench.mixing.mixdown import write_wav

    say = progress or (lambda stage, info=None: None)
    out = Path(song_dir).expanduser()
    session = json.loads((out / "session.json").read_text())
    score = json.loads((out / "score.json").read_text())
    resolved = json.loads((out / "spec.resolved.json").read_text()) if (out / "spec.resolved.json").exists() else {}
    ref = reference_lufs if reference_lufs is not None else resolved.get("reference_lufs", REFERENCE_LUFS)
    spb = 60.0 / score["bpm"]
    sections = [(s["name"], s["start"] * spb, s["end"] * spb) for s in score.get("sections", [])]
    length = session.get("clip_beats") or score.get("length_beats")
    tracks = {}
    for name, t in session["tracks"].items():
        preset = t.get("preset")
        tracks[name] = {"id": t["id"], "volume": t["volume"], "stem": t.get("stem"), "preset": preset,
                        "role": PRESETS.get(preset, {}).get("role") if preset else None,
                        "level": (levels or {}).get(name, t.get("level"))}
    if not sections:
        sections = [("song", 0.0, length * spb)]

    project = Path(session["engine"]["project"])
    daw = MagdaServeAdapter(require_cli(cli_path), project)
    renders = out / "tracks"
    renders.mkdir(exist_ok=True)
    measured, audio, sr = {}, {}, 44100
    try:
        await daw._ensure_session()
        for i, (name, t) in enumerate(tracks.items(), 1):
            path = renders / f"{name}.wav"
            await daw.render(RenderRequest(output_path=path, start_beats=0.0, length_beats=length,
                                           track_ids=[t["id"]]))
            x, sr = read_wav(path)
            audio[name] = x
            measured[name] = {"sections": measure_track(x, sr, sections)}
            say("track", {"name": name, "done": i, "of": len(tracks)})
        rows, shift = plan(tracks, measured, ref)
        report: dict[str, Any] = {"song": str(out), "reference_lufs": ref, "reference_shift_db": shift,
                                  "applied": False, "tracks": rows,
                                  "spec_patch": {"tracks": [{"name": r["track"], "volume": r["new_volume"]}
                                                            for r in rows]}}
        if apply:
            await daw.set_track_volumes({tracks[r["track"]]["id"]: r["new_volume"] for r in rows})
            await daw.save_project()
            stems: dict[str, np.ndarray] = {}
            for r in rows:
                ratio = r["new_volume"] / r["volume"] if r["volume"] > 0 else 0.0
                y = audio[r["track"]] * ratio
                key = r["stem"] or "other"
                stems[key] = y if key not in stems else stems[key] + y
            (out / "stems").mkdir(exist_ok=True)
            for stem, y in stems.items():
                write_wav(out / "stems" / f"{stem}.wav", y, sr, bits=32)
            write_wav(out / "mix.wav", sum(stems.values()), sr, bits=32)
            for r in rows:
                session["tracks"][r["track"]]["volume"] = r["new_volume"]
            session.setdefault("history", []).append({"balance": {r["track"]: r["new_volume"] for r in rows}})
            (out / "session.json").write_text(json.dumps(session, indent=1, default=str))
            report["applied"] = True
            report["peak_dbfs"] = round(20 * math.log10(max(float(np.abs(sum(stems.values())).max()), 1e-12)), 2)
    finally:
        await daw.shutdown()
    (out / "balance.json").write_text(json.dumps(report, indent=1))
    say("done", {"tracks": len(rows), "applied": apply})
    return report
