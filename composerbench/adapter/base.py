"""DawAdapter abstraction (spec §9) and capability discovery (spec §10).

Higher-level ComposerBench reasoning must never depend on MAGDA internals;
it programs against this interface only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
import math
from typing import Any

from composerbench.model import Clip, Device, Note, ProjectInfo, Track


@dataclass(frozen=True)
class AdapterCapabilities:
    """What this adapter/workstation can do. Agents must check before acting."""

    effects: bool = False
    semantic_roles: bool = False
    midi_edit: bool = False
    audio_clips: bool = False
    plugin_parameters: bool = False
    offline_render: bool = False
    section_render: bool = False
    automation: bool = False
    undo: bool = False
    snapshots: bool = False
    # Agent-ergonomics features (ComposerBench x MAGDA serve protocol 2)
    bulk_notes: bool = False          # add/replace a whole clip's notes in one request
    explicit_save: bool = False       # edits need not save per command; save_project() persists
    parameter_labels: bool = False    # parameters report choices / display text
    external_plugins: bool = False    # AU/VST3/LV2 can be hosted
    plugin_persistence: bool = False  # hosted plugins (and their state) are saved in the project

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass
class RenderRequest:
    """A render job. `start_beats`/`length_beats` of None means whole project."""

    output_path: Path
    start_beats: float | None = None
    length_beats: float | None = None
    track_ids: list[str] | None = None  # None = full mix
    bit_depth: int = 24

    def __post_init__(self):
        if self.bit_depth not in (16, 24):
            raise ValueError('render bit_depth must be 16 or 24')
        if self.start_beats is not None and (not math.isfinite(self.start_beats) or self.start_beats < 0):
            raise ValueError('render start must be finite and nonnegative')
        if self.length_beats is not None and (not math.isfinite(self.length_beats) or self.length_beats <= 0):
            raise ValueError('render length must be finite and positive')


class DawAdapter(ABC):
    """Async control surface over a real or emulated DAW project."""

    # ---- capability discovery -------------------------------------------------
    @abstractmethod
    def capabilities(self) -> AdapterCapabilities: ...

    # ---- queries ---------------------------------------------------------------
    @abstractmethod
    async def get_project(self) -> ProjectInfo: ...

    @abstractmethod
    async def get_tracks(self) -> list[Track]: ...

    @abstractmethod
    async def get_track(self, track_id: str) -> Track: ...

    @abstractmethod
    async def get_clips(self, track_id: str | None = None) -> list[Clip]: ...

    @abstractmethod
    async def get_notes(self, track_id: str, clip_id: str | None = None) -> list[Note]: ...

    @abstractmethod
    async def get_devices(self, track_id: str) -> list[Device]: ...

    @abstractmethod
    async def get_parameters(self, track_id: str, device_id: str) -> dict[str, Any]: ...

    # ---- edits -----------------------------------------------------------------
    @abstractmethod
    async def create_track(self, name: str) -> Track: ...

    @abstractmethod
    async def delete_track(self, track_id: str) -> None: ...

    @abstractmethod
    async def create_clip(
        self,
        track_id: str,
        name: str,
        start: float,
        length: float,
    ) -> Clip: ...

    @abstractmethod
    async def add_notes(self, track_id: str, clip_id: str, notes: list[Note]) -> int: ...

    async def set_notes(self, track_id: str, clip_id: str, notes: list[Note]) -> int:
        """Replace every note in a clip with ``notes`` (timeline-absolute starts).

        One call per clip instead of thousands of per-note edits. Adapters
        without a native bulk path may emulate it; the default refuses.
        """
        from composerbench.errors import UnsupportedOperation
        raise UnsupportedOperation(reason="set-notes-not-supported", adapter=type(self).__name__)

    @abstractmethod
    async def modify_notes(
        self,
        track_id: str,
        clip_id: str,
        note_indices: list[int],
        *,
        transpose: int = 0,
        velocity_scale: float = 1.0,
        time_scale: float = 1.0,
    ) -> int: ...

    @abstractmethod
    async def delete_notes(self, track_id: str, clip_id: str, note_indices: list[int]) -> int: ...

    @abstractmethod
    async def set_track_volume(self, track_id: str, volume: float) -> None: ...

    async def set_track_volumes(self, volumes: dict[str, float]) -> None:
        """Several faders at once (default: one call each; engines may batch)."""
        for track_id, volume in volumes.items():
            await self.set_track_volume(track_id, volume)

    @abstractmethod
    async def load_device(self, track_id: str, device_name: str) -> Device: ...

    @abstractmethod
    async def set_parameter(self, track_id: str, device_id: str, parameter: str, value: float) -> None: ...

    async def set_parameters(self, track_id: str, device_id: str, values: dict[str, Any]) -> list[dict]:
        """Set several parameters on one device; one result per parameter.

        Default: one set_parameter call each, failures reported rather than raised.
        """
        results = []
        for name, value in values.items():
            try:
                await self.set_parameter(track_id, device_id, name, value)
                results.append({"param": name, "requested": value, "ok": True})
            except Exception as e:  # noqa: BLE001 - reported per parameter
                results.append({"param": name, "requested": value, "ok": False,
                                "error": f"{type(e).__name__}: {e}"[:300]})
        return results

    @abstractmethod
    async def automate_parameter(
        self,
        track_id: str,
        device_id: str,
        parameter: str,
        points: list[tuple[float, float]],
    ) -> None: ...

    # ---- render / safety ---------------------------------------------------------
    @abstractmethod
    async def render(self, request: RenderRequest) -> Path: ...

    @abstractmethod
    async def save_project(self) -> None: ...

    @abstractmethod
    async def snapshot(self, label: str) -> str: ...

    @abstractmethod
    async def restore_snapshot(self, snapshot_id: str) -> None: ...

    @abstractmethod
    async def undo(self) -> None: ...

    @abstractmethod
    async def redo(self) -> None: ...
