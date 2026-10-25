"""Declarative song specs: what to build in MAGDA and how to render it.

A spec names tracks (a preset and/or an explicit instrument and FX chain), the
notes each track plays (inline, or a part of a Score), fader levels and the
stem each track belongs to. ``resolve()`` merges presets and overrides into
concrete tracks; ``check_song()`` (in build.py) preflights the result against
the device catalog without touching the engine.

Minimal JSON::

    {"title": "demo", "score": "score.json",
     "tracks": [{"name": "kick", "preset": "kick_punch"},
                {"name": "lead", "preset": "lead_glide", "params": {"Glide": 70},
                 "volume": 0.28}]}
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from composerbench.composition.score import Score
from composerbench.devices.presets import get_preset

ParamValue = float | int | str | dict[str, Any]


class DeviceSpec(BaseModel):
    """One instrument or effect. ``device`` = built-in id, ``plugin`` = hosted plugin."""
    model_config = ConfigDict(extra="forbid")
    device: str | None = None
    plugin: str | None = None
    params: dict[str, ParamValue] = Field(default_factory=dict)
    init_wait_seconds: float = Field(default=0.0, ge=0, le=60)

    @model_validator(mode="after")
    def _one_kind(self):
        if bool(self.device) == bool(self.plugin):
            raise ValueError("give exactly one of 'device' (built-in) or 'plugin' (hosted)")
        return self

    @property
    def label(self) -> str:
        return self.device or self.plugin or "?"


class NoteSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start: float = Field(ge=0)
    duration: float = Field(gt=0)
    pitch: int = Field(ge=0, le=127)
    velocity: int = Field(default=100, ge=1, le=127)


class TrackSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    preset: str | None = None
    instrument: DeviceSpec | None = None
    params: dict[str, ParamValue] = Field(default_factory=dict,
                                          description="merged into the instrument's params")
    fx: list[DeviceSpec] | None = Field(default=None, description="replaces the preset's chain")
    fx_params: dict[int, dict[str, ParamValue]] = Field(default_factory=dict,
                                                        description="merged into chain entries by index")
    volume: float | None = Field(default=None, ge=0, le=2, description="linear fader; wins over level")
    level: float | None = Field(default=None, ge=-40, le=12,
                                description="loudness in LU relative to the kick (0); sets the fader from the "
                                            "preset's calibrated loudness (devices/preset_levels.json)")
    pan: float | None = Field(default=None, ge=-1, le=1, description="engine pan (protocol 3)")
    stem: str | None = None
    part: str | None = Field(default=None, description="score part to play (default: track name)")
    notes: list[NoteSpec] | None = None
    transpose: int = 0


class RenderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mix: bool = True
    stems: bool | list[str] = True
    bit_depth: Literal[16, 24] = 24
    tail_beats: float = Field(default=4.0, ge=0)
    min_free_mb: float = Field(default=150.0, ge=0, description="keep at least this much disk free")


class SongSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    bpm: float | None = Field(default=None, gt=0, le=400)
    beats_per_bar: int = 4
    length_beats: float | None = Field(default=None, gt=0)
    score: str | dict | None = None
    reference_lufs: float = Field(default=-23.5, ge=-40, le=-6,
                                  description="loudness a level-0 part (the kick) aims for in the engine render")
    tracks: list[TrackSpec] = Field(min_length=1)
    render: RenderSpec = Field(default_factory=RenderSpec)
    out_dir: str | None = None

    @model_validator(mode="after")
    def _unique_names(self):
        names = [t.name for t in self.tracks]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"track names must be unique: {dup}")
        return self


class ResolvedTrack(BaseModel):
    name: str
    instrument: DeviceSpec
    fx: list[DeviceSpec]
    volume: float
    stem: str
    notes: list[NoteSpec]
    preset: str | None = None
    level: float | None = None
    pan: float | None = None
    volume_source: str = "default"   # volume | level | preset level | preset volume | default
    warnings: list[str] = Field(default_factory=list)


def load_spec(source: str | Path | dict) -> tuple[SongSpec, Path]:
    """A spec from a dict, a JSON string or a file; returns (spec, base dir for relative paths)."""
    if isinstance(source, dict):
        return SongSpec.model_validate(source), Path.cwd()
    text = str(source)
    if text.lstrip().startswith("{"):
        return SongSpec.model_validate_json(text), Path.cwd()
    path = Path(text).expanduser()
    return SongSpec.model_validate_json(path.read_text()), path.parent


def load_score(spec: SongSpec, base: Path) -> Score | None:
    if spec.score is None:
        return None
    if isinstance(spec.score, dict):
        return Score.from_dict(spec.score)
    path = Path(spec.score).expanduser()
    return Score.load(path if path.is_absolute() else base / path)


def resolve(spec: SongSpec, score: Score | None = None) -> list[ResolvedTrack]:
    """Presets + overrides -> concrete tracks with their notes."""
    from composerbench.devices.levels import load_calibration
    calibration = load_calibration()
    out = []
    for t in spec.tracks:
        base = get_preset(t.preset) if t.preset else {}
        if t.instrument is not None:
            inst = copy.deepcopy(t.instrument)
        elif base:
            inst = DeviceSpec.model_validate(base["instrument"])
        else:
            raise ValueError(f"track {t.name!r}: give a preset or an instrument")
        inst.params = {**inst.params, **t.params}
        if t.fx is not None:
            chain = [copy.deepcopy(f) for f in t.fx]
        else:
            chain = [DeviceSpec.model_validate(f) for f in base.get("fx", [])]
        for idx, overrides in t.fx_params.items():
            if not 0 <= int(idx) < len(chain):
                raise ValueError(f"track {t.name!r}: fx_params index {idx} but the chain has {len(chain)} devices")
            chain[int(idx)].params = {**chain[int(idx)].params, **overrides}
        if t.notes is not None:
            notes = list(t.notes)
        elif score is not None and (t.part or t.name) in score.parts:
            notes = [NoteSpec.model_validate(n) for n in score.parts[t.part or t.name]]
        else:
            notes = []
        if t.transpose:
            notes = [n.model_copy(update={"pitch": n.pitch + t.transpose}) for n in notes]
        volume, source, level, warnings = _fader(t, base, spec.reference_lufs, calibration)
        out.append(ResolvedTrack(name=t.name, instrument=inst, fx=chain, volume=volume, volume_source=source,
                                 level=level, pan=t.pan, warnings=warnings,
                                 stem=t.stem or base.get("stem", "other"), notes=notes, preset=t.preset))
    return out


def _fader(t: TrackSpec, base: dict, reference_lufs: float, calibration: dict) -> tuple[float, str, float | None, list]:
    """Fader for a track: explicit volume, else its level (or its preset's level) through the
    preset's calibrated loudness, else the preset's stored volume."""
    from composerbench.devices import levels
    if t.volume is not None:
        return t.volume, "volume", t.level, []
    level = t.level if t.level is not None else levels.default_level(t.preset, base.get("role"))
    unity = levels.lufs_at_unity(t.preset, calibration)
    warnings = []
    if level is not None and unity is not None:
        volume, clamped = levels.volume_for_level(level, unity, reference_lufs)
        if clamped:
            warnings.append(f"{t.name}: level {level} needs more than the fader's +6 dB; clamped to 2.0")
        return volume, ("level" if t.level is not None else "preset level"), level, warnings
    if t.level is not None:
        warnings.append(f"{t.name}: level ignored - preset {t.preset!r} has no calibrated loudness")
    if base.get("volume") is not None:
        return base["volume"], "preset volume", level, warnings
    return 0.5, "default", level, warnings


def song_length(spec: SongSpec, score: Score | None, tracks: list[ResolvedTrack]) -> float:
    """Musical length in beats: explicit, else the score's, else the last note, rounded up to a bar."""
    if spec.length_beats:
        return float(spec.length_beats)
    ends = [n.start + n.duration for t in tracks for n in t.notes]
    if score is not None:
        ends.append(score.length_beats)
    bpb = spec.beats_per_bar
    last = max(ends) if ends else bpb
    return float(-(-last // bpb) * bpb)


def song_bpm(spec: SongSpec, score: Score | None) -> float:
    if spec.bpm:
        return float(spec.bpm)
    if score is not None:
        return float(score.bpm)
    raise ValueError("set 'bpm' in the spec or provide a score")


def to_json(spec: SongSpec) -> str:
    return json.dumps(spec.model_dump(exclude_none=True), indent=1)
