"""FakeDawAdapter (spec §89): fast in-memory DAW emulation for tests.

Emulates enough project state (tracks/clips/notes/devices/parameters,
undo stack, snapshots, WAV rendering via stdlib `wave`) to exercise
ComposerBench planning/evaluation logic without the real workstation.

NOT proof of DAW integration (spec §113) — integration is only proven
against the real MAGDA adapter.
"""

from __future__ import annotations

import copy
import io
import math
import struct
import uuid
import wave
from pathlib import Path
from typing import Any

from composerbench.adapter.base import AdapterCapabilities, DawAdapter, RenderRequest
from composerbench.errors import (
    EntityNotFound,
    ParameterOutOfRange,
    UnsupportedOperation,
)
from composerbench.model import Clip, Device, Note, ProjectInfo, Track


def _fuzzy_matches(requested: str, candidates: list[str]) -> list[str]:
    req = requested.lower()
    return [c for c in candidates if req in c.lower() or c.lower() in req]


class FakeDawAdapter(DawAdapter):
    def __init__(self, tempo_bpm: float = 120.0, state_file: Path | None = None) -> None:
        self._tempo = tempo_bpm
        self._title = "Untitled"
        self._tracks: dict[str, Track] = {}
        self._clips: dict[str, Clip] = {}
        self._automation: dict[tuple[str, str, str], list[tuple[float, float]]] = {}
        # device_id -> {"params": {name: {"value": float, "range": [lo, hi]}}, ...}
        self._devices: dict[str, dict[str, Any]] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._undo_stack: list[dict[str, Any]] = []
        self._redo_stack: list[dict[str, Any]] = []
        self._revision = 0
        self.state_file = Path(state_file) if state_file else None
        self._load_state()

    # ---- persistence ------------------------------------------------------------
    def _load_state(self) -> None:
        import json

        if not self.state_file or not self.state_file.exists():
            return
        data = json.loads(self.state_file.read_text())
        self._tempo = data.get("tempo", self._tempo)
        self._title = data.get("title", self._title)
        self._tracks = {t["id"]: Track.model_validate(t) for t in data.get("tracks", [])}
        self._clips = {c["id"]: Clip.model_validate(c) for c in data.get("clips", [])}
        self._devices = data.get("devices", {})
        self._revision = data.get("revision", 0)

    def _save_state(self) -> None:
        import json

        if not self.state_file:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({
            "tempo": self._tempo,
            "title": self._title,
            "revision": self._revision,
            "tracks": [t.model_dump() for t in self._tracks.values()],
            "clips": [c.model_dump() for c in self._clips.values()],
            "devices": self._devices,
        }, indent=1))

    # ---- capability discovery -------------------------------------------------
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            midi_edit=True,
            audio_clips=False,
            plugin_parameters=True,
            offline_render=True,
            section_render=True,
            automation=True,
            undo=True,
            snapshots=True,
            bulk_notes=True,
            explicit_save=True,
            parameter_labels=True,
        )

    # ---- internal helpers -------------------------------------------------------
    def _bump(self, undo_entry: dict | None = None) -> None:
        self._revision += 1
        if undo_entry is not None:
            self._undo_stack.append(undo_entry)
            self._redo_stack.clear()
        self._save_state()

    def _require_track(self, track_id: str) -> Track:
        track = self._resolve_track(track_id)
        if track is None:
            raw = track_id.removeprefix("track:")
            matches = _fuzzy_matches(raw, [t.name for t in self._tracks.values()])
            raise EntityNotFound("track", track_id, matches)
        return track

    def _resolve_track(self, track_id: str) -> Track | None:
        track = self._tracks.get(track_id)
        if track is not None:
            return track
        raw = track_id.removeprefix("track:")
        return next((t for t in self._tracks.values() if t.name == raw), None)

    def _require_clip(self, clip_id: str) -> Clip:
        clip = self._clips.get(clip_id)
        if clip is None:
            matches = _fuzzy_matches(clip_id.removeprefix("clip:"), [c.name for c in self._clips.values()])
            raise EntityNotFound("clip", clip_id, matches)
        return clip

    # ---- queries -----------------------------------------------------------------
    async def get_project(self) -> ProjectInfo:
        length = max((c.start + c.length for c in self._clips.values()), default=0.0)
        return ProjectInfo(
            title=self._title,
            tempo_bpm=self._tempo,
            length_beats=length,
            revision=self._revision,
            tracks=[t.id for t in self._tracks.values()],
        )

    async def get_tracks(self) -> list[Track]:
        return list(self._tracks.values())

    async def get_track(self, track_id: str) -> Track:
        return copy.deepcopy(self._require_track(track_id))

    async def get_clips(self, track_id: str | None = None) -> list[Clip]:
        if track_id is not None:
            track_id = self._require_track(track_id).id
        clips = [c for c in self._clips.values() if track_id is None or c.track_id == track_id]
        return [copy.deepcopy(c) for c in sorted(clips, key=lambda c: c.start)]

    async def get_notes(self, track_id: str, clip_id: str | None = None) -> list[Note]:
        track = self._require_track(track_id)
        notes: list[Note] = []
        for clip in await self.get_clips(track.id):
            if clip_id is not None and clip.id != clip_id:
                continue
            notes.extend(clip.notes)
        return notes

    async def get_devices(self, track_id: str) -> list[Device]:
        track = self._require_track(track_id)
        return copy.deepcopy(track.devices)

    async def get_parameters(self, track_id: str, device_id: str) -> dict[str, Any]:
        self._require_track(track_id)
        dev = self._devices.get(device_id)
        if dev is None:
            raise EntityNotFound("device", device_id)
        return copy.deepcopy(dev["params"])

    # ---- edits -----------------------------------------------------------------
    async def create_track(self, name: str) -> Track:
        if any(t.name == name for t in self._tracks.values()):
            raise ValueError(f"track named {name!r} already exists")
        track = Track(id=f"track:{name}", name=name)
        self._tracks[track.id] = track
        self._bump({"kind": "create_track"})
        return copy.deepcopy(track)

    async def set_project_tempo(self, bpm: float) -> None:
        if bpm <= 0:
            raise ParameterOutOfRange(parameter="tempo", received=bpm, allowed=[">0"])
        self._tempo = float(bpm)
        self._bump()

    async def delete_track(self, track_id: str) -> None:
        self._require_track(track_id)
        del self._tracks[track_id]
        for cid in [c.id for c in self._clips.values() if c.track_id == track_id]:
            del self._clips[cid]
        self._bump({"kind": "delete_track"})

    async def create_clip(self, track_id: str, name: str, start: float, length: float) -> Clip:
        self._require_track(track_id)
        if length <= 0:
            raise ParameterOutOfRange(parameter="length", received=length, allowed=[">0"])
        clip = Clip(id=f"clip:{name}" if not name.startswith("clip:") else name, name=name,
                    track_id=track_id, start=start, length=length)
        if clip.id in self._clips:
            clip.id += ":" + uuid.uuid4().hex
        self._clips[clip.id] = clip
        self._bump({"kind": "create_clip"})
        return copy.deepcopy(clip)

    async def add_notes(self, track_id: str, clip_id: str, notes: list[Note]) -> int:
        clip = self._require_clip(clip_id)
        self._require_track(track_id)
        if clip.track_id != track_id:
            raise EntityNotFound("clip-on-track", f"{clip_id}@{track_id}")
        for n in notes:
            if n.pitch < 0 or n.pitch > 127:
                raise ParameterOutOfRange(parameter="pitch", received=n.pitch, allowed=[0, 127])
        clip.notes.extend(copy.deepcopy(notes))
        self._bump()
        return len(notes)

    async def set_notes(self, track_id: str, clip_id: str, notes: list[Note]) -> int:
        clip = self._require_clip(clip_id)
        self._require_track(track_id)
        if clip.track_id != track_id:
            raise EntityNotFound("clip-on-track", f"{clip_id}@{track_id}")
        for n in notes:
            if not clip.start - 1e-6 <= n.start < clip.start + clip.length:
                raise ParameterOutOfRange(parameter="note.start", received=n.start,
                                          allowed=[clip.start, clip.start + clip.length])
        clip.notes = copy.deepcopy(list(notes))
        self._bump({"kind": "set_notes"})
        return len(notes)

    async def modify_notes(
        self,
        track_id: str,
        clip_id: str,
        note_indices: list[int],
        *,
        transpose: int = 0,
        velocity_scale: float = 1.0,
        time_scale: float = 1.0,
    ) -> int:
        clip = self._require_clip(clip_id)
        changed = 0
        for i in note_indices:
            if i < 0 or i >= len(clip.notes):
                continue
            n = clip.notes[i]
            n.pitch = max(0, min(127, n.pitch + transpose))
            n.velocity = max(1, min(127, round(n.velocity * velocity_scale)))
            n.start *= time_scale
            n.duration *= time_scale
            changed += 1
        self._bump()
        return changed

    async def delete_notes(self, track_id: str, clip_id: str, note_indices: list[int]) -> int:
        clip = self._require_clip(clip_id)
        keep = [n for i, n in enumerate(clip.notes) if i not in set(note_indices)]
        removed = len(clip.notes) - len(keep)
        clip.notes = keep
        self._bump()
        return removed

    async def set_track_volume(self, track_id: str, volume: float) -> None:
        track = self._require_track(track_id)
        if volume < 0.0 or volume > 2.0:
            raise ParameterOutOfRange(parameter="volume", received=volume, allowed=[0.0, 2.0])
        track.volume = volume
        self._bump()

    async def load_device(self, track_id: str, device_name: str) -> Device:
        track = self._require_track(track_id)
        device_id = f"device:{device_name.lower().replace(' ', '-')}:{uuid.uuid4().hex}"
        device = Device(id=device_id, name=device_name, type="instrument")
        track.devices.append(device)
        self._devices[device_id] = {
            "type": "instrument",
            "params": {
                "level": {"value": 0.8, "range": [0.0, 1.0]},
                "cutoff": {"value": 0.5, "range": [0.0, 1.0]},
                "attack": {"value": 0.1, "range": [0.0, 1.0]},
                "release": {"value": 0.3, "range": [0.0, 1.0]},
                "mode": {"value": 0.0, "range": [0.0, 2.0],
                         "choices": ["Poly", "Mono", "Legato"]},
            },
        }
        self._bump()
        return device

    async def set_parameter(self, track_id: str, device_id: str, parameter: str, value: float | str) -> None:
        self._require_track(track_id)
        dev = self._devices.get(device_id)
        if dev is None or parameter not in dev["params"]:
            raise EntityNotFound("parameter", f"{device_id}.{parameter}")
        if isinstance(value, str):
            from composerbench.params import resolve_value
            value = resolve_value({"name": parameter, **dev["params"][parameter]}, value)
        lo, hi = dev["params"][parameter]["range"]
        if value < lo or value > hi:
            raise ParameterOutOfRange(
                device=device_id, parameter=parameter, received=value, allowed=[lo, hi]
            )
        dev["params"][parameter]["value"] = value
        self._bump()

    async def automate_parameter(
        self,
        track_id: str,
        device_id: str,
        parameter: str,
        points: list[tuple[float, float]],
    ) -> None:
        self._require_track(track_id)
        dev = self._devices.get(device_id)
        if dev is None or parameter not in dev["params"]:
            raise EntityNotFound("parameter", f"{device_id}.{parameter}")
        key = (track_id, device_id, parameter)
        existing = self._automation.setdefault(key, [])
        existing.extend(points)
        self._bump()

    # ---- render / safety ---------------------------------------------------------
    async def render(self, request: RenderRequest) -> Path:
        if request.track_ids is not None:
            raise UnsupportedOperation(reason='fake-render-does-not-model-stems')
        info = await self.get_project()
        total_beats = info.length_beats
        start = request.start_beats or 0.0
        length = request.length_beats if request.length_beats is not None else max(total_beats - start, 0.0)
        if length <= 0:
            raise UnsupportedOperation(reason="nothing-to-render", start_beats=start)
        seconds = length * 60.0 / self._tempo
        sample_rate = 44100
        n_samples = int(seconds * sample_rate)
        out = Path(request.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # Write a deterministic quiet sine "render" so downstream audio-analysis
        # code has real bytes to chew on; the fake engine has no musical DSP.
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(request.bit_depth // 8)
            wf.setframerate(sample_rate)
            buf = io.BytesIO()
            chunk = 4096
            written = 0
            phase = 0.0
            while written < n_samples:
                k = min(chunk, n_samples - written)
                samples = b"".join(
                    (int(3000.0 * math.sin(phase + 440.0 * 2 * math.pi * (written + i) / sample_rate))
                     * 2**(request.bit_depth - 16)).to_bytes(request.bit_depth // 8, 'little', signed=True)
                    for i in range(k)
                )
                buf.write(samples)
                written += k
            wf.writeframes(buf.getvalue())
        return out

    async def save_project(self) -> None:
        self._bump()

    async def snapshot(self, label: str) -> str:
        snap_id = f"snap:{label}:{uuid.uuid4().hex[:6]}"
        self._snapshots[snap_id] = {
            "tempo": self._tempo,
            "title": self._title,
            "revision": self._revision,
            "tracks": copy.deepcopy(self._tracks),
            "clips": copy.deepcopy(self._clips),
            "devices": copy.deepcopy(self._devices),
            "automation": copy.deepcopy(self._automation),
            "undo": copy.deepcopy(self._undo_stack),
            "redo": copy.deepcopy(self._redo_stack),
        }
        return snap_id

    async def restore_snapshot(self, snapshot_id: str) -> None:
        snap = self._snapshots.get(snapshot_id)
        if snap is None:
            raise EntityNotFound("snapshot", snapshot_id)
        self._tempo = snap["tempo"]
        self._title = snap["title"]
        self._tracks = copy.deepcopy(snap["tracks"])
        self._clips = copy.deepcopy(snap["clips"])
        self._devices = copy.deepcopy(snap["devices"])
        self._automation = copy.deepcopy(snap["automation"])
        self._undo_stack = copy.deepcopy(snap.get("undo", []))
        self._redo_stack = copy.deepcopy(snap.get("redo", []))
        self._revision = snap["revision"]
        self._save_state()

    async def undo(self) -> None:
        entry = self._undo_stack.pop() if self._undo_stack else None
        if entry is None:
            raise UnsupportedOperation(reason="undo-stack-empty")
        self._redo_stack.append(entry)
        self._revision += 1

    async def redo(self) -> None:
        entry = self._redo_stack.pop() if self._redo_stack else None
        if entry is None:
            raise UnsupportedOperation(reason="redo-stack-empty")
        self._undo_stack.append(entry)
        self._revision += 1
