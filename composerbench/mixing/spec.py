"""Mix specs: how rendered stems become a finished master.

MAGDA (protocol 2) has no automation, sends, sidechain or master bus, so the
production moves that need them happen here, on the stems, driven by a JSON
spec an agent can edit and re-run in seconds.

Times are beats when ``bpm`` is known (default) or seconds with
``"time_unit": "seconds"``. Anywhere a time is expected you may also write a
section reference: ``"drop1"`` (its start), ``"drop1.end"``, ``"pre1.end-1"``
(one beat before the end of pre1), ``"chorus2+8"``.

Automation points are ``[time, value]`` (ramp from the previous point) or
``[time, value, "jump"]`` (hold the previous value until ``time``, then step):
``[["intro", 400], ["intro.end", 18000, "jump"]]`` keeps the intro dark and
opens the filter exactly at the verse.

    {
      "stems_dir": "stems", "score": "score.json",
      "stems": {"drums": {"gain_db": 0}, "bass": {"gain_db": -1},
                "synths": {"automation": {"lowpass_hz": [["intro", 400], ["verse1", 18000]]}}},
      "sidechain": [{"trigger": {"part": "kick"}, "targets": ["bass", "synths"], "depth_db": 4,
                     "section_depth_db": {"drop1": 8}}],
      "cuts": [{"start": "pre1.end-1", "end": "pre1.end", "keep": ["fx"]}],
      "edits": [{"type": "tape_stop", "start": "outro+8", "length": 2}],
      "bus": {"eq": [{"type": "highpass", "freq": 25}], "glue": {"threshold_db": -14, "ratio": 2}},
      "master": {"target_lufs": -9, "ceiling_dbtp": -1}
    }
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Time = float | int | str
# [time, value] ramps from the previous point; [time, value, "jump"] holds the
# previous value until ``time`` and then steps (e.g. open a filter at the drop).
Point = tuple[Time, float] | tuple[Time, float, Literal["jump", "ramp"]]


class EQBand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["lowpass", "highpass", "bandpass", "bell", "lowshelf", "highshelf", "notch"] = "bell"
    freq: float = Field(gt=0)
    q: float = Field(default=0.7071, gt=0)
    gain_db: float = 0.0
    order: Literal[2, 4, 6, 8] = 2


class Automation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gain_db: list[Point] = Field(default_factory=list)
    lowpass_hz: list[Point] = Field(default_factory=list)
    highpass_hz: list[Point] = Field(default_factory=list)
    filter_q: float = Field(default=0.7071, gt=0)


class StemSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = None            # default: <stems_dir>/<name>.wav
    gain_db: float = 0.0
    mute: bool = False
    eq: list[EQBand] = Field(default_factory=list)
    automation: Automation = Field(default_factory=Automation)
    width: float | None = Field(default=None, ge=0, le=3)
    mono_below_hz: float | None = Field(default=None, gt=0)
    pan: float | None = Field(default=None, ge=-1, le=1)
    offset: Time = 0.0                 # place the stem later on the timeline


class Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part: str | None = None            # a part in score.json (exact note times)
    times: list[Time] | None = None    # explicit trigger times
    stem: str | None = None            # detect onsets in this stem's audio
    band_hz: tuple[float, float] = (30.0, 150.0)
    threshold_db: float = -24.0
    min_velocity: int = 1


class Sidechain(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: Trigger
    targets: list[str] = Field(min_length=1)
    depth_db: float = Field(default=4.0, ge=0, le=40)
    section_depth_db: dict[str, float] = Field(default_factory=dict)
    attack_ms: float = Field(default=3.0, gt=0)
    hold_ms: float = Field(default=15.0, ge=0)
    release_ms: float = Field(default=140.0, gt=0)
    velocity_scaled: bool = False      # scale depth by trigger velocity / 127


class Cut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: Time
    end: Time
    keep: list[str] = Field(default_factory=list)   # stems that keep playing
    fade_ms: float = Field(default=4.0, ge=0)


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tape_stop", "stutter", "reverse_swell", "telephone", "bitcrush", "gain", "mute"]
    start: Time | None = None
    end: Time | None = None
    length: float | None = Field(default=None, gt=0)   # beats (or seconds)
    stems: list[str] | Literal["mix"] = "mix"
    slice: float | None = Field(default=None, gt=0)     # stutter slice length
    decay_db: float = 0.0
    source_start: Time | None = None                     # reverse_swell: where to copy from
    gain_db: float = 0.0
    lo_hz: float = 400.0
    hi_hz: float = 3200.0
    bits: int = Field(default=8, ge=1, le=24)
    rate_hz: float | None = None
    fade: float = 0.25                                    # beats/seconds of crossfade for telephone/bitcrush
    curve: float = 1.6
    silence_after: bool = False


class Glue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold_db: float = -14.0
    ratio: float = Field(default=2.0, ge=1)
    attack_ms: float = 10.0
    release_ms: float = 150.0
    knee_db: float = 6.0
    makeup_db: float = 0.0


class Bus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eq: list[EQBand] = Field(default_factory=list)
    glue: Glue | None = None
    mono_below_hz: float | None = 120.0
    width: float = 1.0


class Master(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_lufs: float | None = Field(default=-10.0, ge=-30, le=-4)
    ceiling_dbtp: float = Field(default=-1.0, ge=-6, le=-0.1)
    soft_clip: bool = True
    drive_db: float = Field(default=0.0, ge=0, le=12)
    lookahead_ms: float = 1.5
    release_ms: float = 60.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    end: Time | None = None            # trim the master here (after fade_out)


class Export(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basename: str = "master"
    bit_depth: Literal[16, 24, 32] = 24
    mp3: bool = True
    mp3_bitrate: str = "256k"
    flac: bool = False
    spectrogram: bool = True


class MixSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bpm: float | None = Field(default=None, gt=0)
    time_unit: Literal["beats", "seconds"] = "beats"
    score: str | None = None           # score.json written by the song builder
    stems_dir: str = "stems"
    stems: dict[str, StemSpec] = Field(default_factory=dict)
    sections: list[dict] | None = None # [{"name", "start", "end"}] when there is no score
    sidechain: list[Sidechain] = Field(default_factory=list)
    cuts: list[Cut] = Field(default_factory=list)
    edits: list[Edit] = Field(default_factory=list)
    bus: Bus = Field(default_factory=Bus)
    master: Master = Field(default_factory=Master)
    export: Export = Field(default_factory=Export)
    out_dir: str | None = None


_REF = re.compile(r"^\s*([A-Za-z_][\w-]*?)(\.end|\.start)?\s*([+-]\s*\d+(?:\.\d+)?)?\s*$")


class Timeline:
    """Converts spec times (numbers or section references) to seconds."""

    def __init__(self, bpm: float | None, unit: str, sections: list[dict]):
        self.bpm, self.unit = bpm, unit
        self.sections = {s["name"]: s for s in sections}
        if unit == "beats" and not bpm:
            raise ValueError("time_unit 'beats' needs a bpm (or a score that has one)")

    def to_seconds(self, value: Time) -> float:
        """Seconds for a number (in the spec's unit) or a section reference
        (section boundaries are beats; the offset is in the spec's unit)."""
        spb = 60.0 / self.bpm if self.bpm else None
        unit_s = spb if self.unit == "beats" else 1.0
        if isinstance(value, (int, float)):
            return float(value) * unit_s
        m = _REF.match(str(value))
        if not m or m.group(1) not in self.sections:
            raise ValueError(f"cannot resolve time {value!r}; sections: {sorted(self.sections)}")
        if spb is None:
            raise ValueError(f"section reference {value!r} needs a bpm")
        s = self.sections[m.group(1)]
        base = (s["end"] if m.group(2) == ".end" else s["start"]) * spb
        off = float(m.group(3).replace(" ", "")) if m.group(3) else 0.0
        return base + off * unit_s

    def length_seconds(self, value: float | None) -> float | None:
        if value is None:
            return None
        return float(value) * (60.0 / self.bpm if self.unit == "beats" else 1.0)

    def section_seconds(self) -> list[dict]:
        if not self.bpm:
            return []
        k = 60.0 / self.bpm
        return [{"name": n, "start_s": s["start"] * k, "end_s": s["end"] * k} for n, s in self.sections.items()]


def load_mix_spec(source: str | Path | dict) -> tuple[MixSpec, Path]:
    if isinstance(source, dict):
        return MixSpec.model_validate(source), Path.cwd()
    text = str(source)
    if text.lstrip().startswith("{"):
        return MixSpec.model_validate_json(text), Path.cwd()
    path = Path(text).expanduser()
    return MixSpec.model_validate(json.loads(path.read_text())), path.parent


def example_spec() -> dict[str, Any]:
    """A sensible starting mix for a song built by composerbench.song (beats; sections from score.json)."""
    return {
        "score": "score.json", "stems_dir": "stems",
        "stems": {"drums": {}, "bass": {"mono_below_hz": 150}, "synths": {}, "lead": {}, "fx": {}},
        "sidechain": [{"trigger": {"part": "kick"}, "targets": ["bass", "synths"], "depth_db": 4}],
        "bus": {"eq": [{"type": "highpass", "freq": 25, "order": 4}],
                "glue": {"threshold_db": -14, "ratio": 2}, "mono_below_hz": 120},
        "master": {"target_lufs": -10, "ceiling_dbtp": -1},
    }
