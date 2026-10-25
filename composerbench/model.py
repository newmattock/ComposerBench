"""Core data model shared by all DAW adapters (spec §9, §11, §15).

Musical time is expressed in quarter notes (beats) from project start.
Pitches are MIDI note numbers (0-127). Velocities 1-127.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Note(BaseModel):
    """A single MIDI note event.

    Contract: adapter queries (get_notes/get_clips) return timeline-absolute
    `start` values; adapters translate workstation-relative storage at their
    boundary. Action notes passed to adapters are also timeline-absolute.
    """

    model_config = ConfigDict(allow_inf_nan=False)
    start: float = Field(ge=0, description="timeline position in beats")
    duration: float = Field(gt=0, description="length in beats")
    pitch: int = Field(ge=0, le=127)
    velocity: int = Field(ge=1, le=127, default=100)


class Device(BaseModel):
    stable_id: str | None = None
    id: str
    name: str
    type: str = "unknown"  # instrument | effect | other
    format: str = "internal"  # internal | VST3 | AU | ...
    parameters: dict[str, Any] = Field(default_factory=dict)


class Clip(BaseModel):
    stable_id: str | None = None
    id: str
    name: str = ""
    track_id: str
    start: float = Field(ge=0, description="clip start in beats on the timeline")
    length: float = Field(gt=0, description="clip length in beats")
    notes: list[Note] = Field(default_factory=list)


class Track(BaseModel):
    stable_id: str | None = None
    id: str
    name: str
    volume: float = 1.0
    pan: float = 0.0
    muted: bool = False
    devices: list[Device] = Field(default_factory=list)


class ProjectInfo(BaseModel):
    title: str = "Untitled"
    tempo_bpm: float = 120.0
    length_beats: float = 0.0
    revision: int = 0
    tracks: list[str] = Field(default_factory=list)


def beats_per_bar(tempo_bpm: float, time_sig_num: int = 4) -> float:
    """Beats (quarter notes) per bar at the given time signature."""
    return float(time_sig_num)
