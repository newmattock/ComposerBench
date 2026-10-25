"""Preset loudness: what each preset measures at a unity fader, and where it should sit.

Every preset has a *level*: how loud its part should be, in LU relative to the
kick (0 = as loud as the kick; -10 = hats; -1.5 = 808 and supersaws). A song
spec sets the reference the kick aims for in the engine render
(``reference_lufs``, default -23.5, which leaves headroom for the master
stage), so a track's fader follows from::

    volume = 10 ** ((reference_lufs + level - lufs_at_unity) / 20)

``lufs_at_unity`` comes from ``preset_levels.json``: each preset rendered at a
unity fader playing a short probe phrase typical of its role (quarter-note
kicks, two-bar chords, an eighth-note lead line...). Refresh it with
``composerbench presets calibrate`` after changing presets or engine builds.

The probe is a starting point, not the song: density and register change a
part's loudness, so check a built song with the balance pass
(``composerbench song balance`` / ``music_balance``).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

CALIBRATION_FILE = Path(__file__).with_name("preset_levels.json")
REFERENCE_LUFS = -23.5
PROBE_BPM = 120.0
PROBE_BEATS = 8.0

# LU relative to the kick, from the glitterstatic balance pass on real renders
LEVELS: dict[str, float] = {
    "kick_punch": 0.0, "kick_boom": 0.0, "snare_tight": -3.5, "clap_wide": -4.5,
    "hat_closed": -10.0, "hat_open": -12.0, "crash_noise": -8.0, "impact_boom": -5.5, "zap_crushed": -8.0,
    "bass_808": -1.5, "sub_sine": -4.0,
    "supersaw_surge": -1.5, "supersaw_poly": -1.5, "pad_sweep": -6.0, "pluck": -6.0,
    "bell": -7.0, "bell_fm": -9.0,
    "lead_glide": 0.0, "lead_fm_crushed": -8.0, "chop_square": -3.0,
    "riser": -8.5, "noise_swell": -8.5, "static_crush": -12.0, "beep": -8.0,
}
ROLE_LEVELS: dict[str, float] = {
    "kick": 0.0, "snare": -3.5, "clap": -4.5, "hat": -10.0, "crash": -8.0, "impact": -5.5, "zap": -8.0,
    "bass": -1.5, "chords": -1.5, "pad": -6.0, "arp": -6.0, "bell": -9.0, "lead": 0.0, "chop": -3.0,
    "riser": -8.5, "swell": -8.5, "texture": -12.0,
}


def _hits(pitch: int, beats, length: float = 0.25, velocity: int = 110) -> list[dict]:
    return [{"start": float(b), "duration": length, "pitch": pitch, "velocity": velocity} for b in beats]


def _chords(chords, length: float = 3.9, velocity: int = 100) -> list[dict]:
    return [{"start": 4.0 * i, "duration": length, "pitch": p, "velocity": velocity}
            for i, chord in enumerate(chords) for p in chord]


def _line(pitches, step: float, length: float, velocity: int = 100) -> list[dict]:
    n = int(PROBE_BEATS / step)
    return [{"start": i * step, "duration": length, "pitch": pitches[i % len(pitches)], "velocity": velocity}
            for i in range(n)]


def probe_notes(role: str | None, preset: str = "") -> list[dict]:
    """The probe phrase (8 beats) used to calibrate a preset of this role."""
    if preset == "beep":
        return _hits(75, range(0, 8, 2), 0.45)
    probes = {
        "kick": lambda: _hits(36, range(8)),
        "snare": lambda: _hits(38, (1, 3, 5, 7)),
        "clap": lambda: _hits(39, (1, 3, 5, 7)),
        "hat": lambda: _hits(42, [i * 0.5 for i in range(16)], 0.1, 100),
        "crash": lambda: _hits(49, (0, 4), 2.0),
        "impact": lambda: _hits(36, (0, 4), 2.0, 120),
        "zap": lambda: _hits(72, (0, 2, 4, 6), 0.5),
        "bass": lambda: [{"start": 0.0, "duration": 3.9, "pitch": 28, "velocity": 110},
                         {"start": 4.0, "duration": 3.9, "pitch": 33, "velocity": 110}],
        "chords": lambda: _chords([(57, 60, 64, 67), (53, 57, 60, 64)]),
        "pad": lambda: _chords([(57, 60, 64, 67), (53, 57, 60, 64)]),
        "arp": lambda: _line([60, 64, 67, 72], 0.25, 0.2),
        "bell": lambda: _line([72, 76, 79, 84], 0.5, 0.4),
        "lead": lambda: _line([72, 74, 76, 79, 76, 74, 72, 71], 0.5, 0.45),
        "chop": lambda: _line([76, 79], 0.5, 0.2, 110),
        "riser": lambda: [{"start": 0.0, "duration": 8.0, "pitch": 60, "velocity": 120}],
        "swell": lambda: [{"start": 0.0, "duration": 8.0, "pitch": 60, "velocity": 120}],
    }
    return probes.get(role or "", lambda: [{"start": 0.0, "duration": 8.0, "pitch": 60, "velocity": 100}])()


def load_calibration(path: Path | None = None) -> dict:
    p = Path(path) if path else CALIBRATION_FILE
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"presets": {}}


def default_level(preset: str | None, role: str | None = None) -> float | None:
    if preset in LEVELS:
        return LEVELS[preset]
    return ROLE_LEVELS.get(role or "")


def lufs_at_unity(preset: str | None, calibration: dict | None = None) -> float | None:
    if not preset:
        return None
    entry = (calibration or load_calibration()).get("presets", {}).get(preset)
    value = entry.get("lufs_at_unity") if entry else None
    return value if value is not None and math.isfinite(value) else None


def volume_for_level(level: float, unity_lufs: float, reference_lufs: float = REFERENCE_LUFS,
                     max_volume: float = 2.0) -> tuple[float, bool]:
    """Fader value that puts a part measuring ``unity_lufs`` at a unity fader at
    ``reference_lufs + level``; second value is True when it had to be clamped."""
    volume = 10 ** ((reference_lufs + level - unity_lufs) / 20)
    return round(min(volume, max_volume), 4), volume > max_volume


async def calibrate(cli_path, work_dir: Path | str, presets: list[str] | None = None,
                    out_path: Path | str | None = None, progress=None) -> dict:
    """Render every preset at a unity fader on its probe phrase and record its loudness.

    Builds one track per preset (each its own stem, so a protocol-3 engine
    renders each alone) and measures the probe region of each stem.
    """
    import time

    from composerbench.analysis.wav import read_wav
    from composerbench.devices.presets import PRESETS
    from composerbench.mixing.loudness import integrated_lufs
    from composerbench.song.build import build_song

    names = presets or list(PRESETS)
    tracks = [{"name": n, "preset": n, "volume": 1.0, "stem": n,
               "notes": probe_notes(PRESETS[n].get("role"), n)} for n in names]
    spec = {"title": "preset calibration", "bpm": PROBE_BPM, "length_beats": PROBE_BEATS,
            "tracks": tracks, "render": {"mix": False, "stems": True, "tail_beats": 2}}
    work = Path(work_dir)
    report = await build_song(spec, out_dir=work, cli_path=cli_path, progress=progress)
    result = {"engine": str(cli_path), "generated": time.strftime("%Y-%m-%d"),
              "probe": {"bpm": PROBE_BPM, "beats": PROBE_BEATS}, "presets": {}, "problems": report["problems"]}
    end = int(PROBE_BEATS * 60 / PROBE_BPM * 44100)
    for n in names:
        path = work / "stems" / f"{n}.wav"
        if n not in report["tracks"] or not path.exists():
            continue                     # the preset could not be built here (e.g. a missing plugin)
        x, sr = read_wav(path)
        x = x[: int(PROBE_BEATS * 60 / PROBE_BPM * sr)] if sr else x[:end]
        lufs = integrated_lufs(x, sr)
        result["presets"][n] = {"lufs_at_unity": round(lufs, 2) if math.isfinite(lufs) else None,
                                "role": PRESETS[n].get("role"), "level": default_level(n, PRESETS[n].get("role"))}
    if out_path:
        Path(out_path).write_text(json.dumps(result, indent=1) + "\n")
    return result
