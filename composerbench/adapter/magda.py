"""MagdaAdapter: drives the unmodified headless MAGDA CLI (spec §4 Level 1-2).

Transport semantics (verified against magda-core @ ebd93db0):
  init  <file.mgd>                     create empty project
  exec  <file.mgd> <cmds...> --out X   load -> command batch -> save
  dump  (via exec --dump-json)         full project JSON projection
  render <file.mgd> <out.wav> --from T --to T [--sample-rate N] [--bit-depth N]

Each `exec` is load->edit->save: a batched, file-persisted transaction.
Snapshots are file copies of the .mgd project.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import shutil
import uuid
from pathlib import Path

from composerbench.adapter.base import AdapterCapabilities, DawAdapter, RenderRequest
from composerbench.adapter.instruments import (
    allow_any_instrument,
    audible_instruments_for,
)
from composerbench.errors import (
    ConnectionLost,
    EntityNotFound,
    ComposerBenchError,
    UnsupportedOperation,
)
from composerbench.model import Clip, Device, Note, ProjectInfo, Track

NOTE_NAME_TO_SEMITONE = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


class InvalidNote(ComposerBenchError):
    code = "INVALID_NOTE"


def parse_note_pitch(value: str | int) -> int:
    """'C4'/'F#3'/'Bb2' or MIDI number -> MIDI number."""
    if isinstance(value, int):
        return value
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d+)", value.strip())
    if not m:
        raise InvalidNote(requested=value)
    letter, accidental, octave = m.groups()
    semitone = NOTE_NAME_TO_SEMITONE[letter.lower()]
    if accidental == "#":
        semitone += 1
    elif accidental == "b":
        semitone -= 1
    return semitone + (int(octave) + 1) * 12


class MagdaCliAdapter(DawAdapter):
    def __init__(self, cli_path: Path | str, project_path: Path | str):
        self.cli = Path(cli_path).expanduser().resolve()
        self.project = Path(project_path).expanduser().resolve()
        self._snapshot_dir = self.project.parent / ".musicbench" / "snapshots"
        if not self.cli.exists():
            raise EntityNotFound("magda-cli", str(self.cli))
        self._adopt_wrapper_path()
        # Stable ComposerBench names -> workstation numeric ids (spec §11).
        # The CLI cannot rename clips, so ComposerBench owns the naming layer.
        self._aliases: dict[str, int] = {}
        self._alias_file = self.project.parent / ".musicbench" / "aliases.json"
        self._track_devices: dict[str, list[Device]] = {}
        # Clips rebuilt by set_notes on engines without a bulk note op get a
        # new engine id; callers holding the old id are forwarded to the new one.
        self._clip_remap: dict[int, int] = {}
        self._load_device_registry()
        self._load_aliases()

    def _load_aliases(self) -> None:
        import json

        if self._alias_file.exists():
            self._aliases = {
                k: int(v)
                for k, v in json.loads(self._alias_file.read_text()).items()
            }

    def _save_aliases(self) -> None:
        import json

        self._alias_file.parent.mkdir(parents=True, exist_ok=True)
        self._alias_file.write_text(json.dumps(self._aliases))

    def _remember(self, name: str, numeric_id: int) -> None:
        self._aliases[name] = numeric_id
        self._save_aliases()

    def _adopt_wrapper_path(self) -> None:
        """saveProjectAs nests <dir>/Foo/Foo.mgd; prefer the nested file
        when only it exists so subsequent loads hit the real file."""
        if self.project.exists():
            return
        nested = self.project.parent / self.project.stem / self.project.name
        if nested.exists():
            self.project = nested.resolve()
            self._snapshot_dir = self.project.parent / ".musicbench" / "snapshots"
            self._alias_file = self.project.parent / ".musicbench" / "aliases.json"

    async def initialize(self) -> None:
        """Create an empty project at self.project via the CLI."""
        await self._run_cli("init", str(self.project))
        self._adopt_wrapper_path()

    # ---- process plumbing ------------------------------------------------------
    async def _run_cli(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            str(self.cli), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=900)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            raise ConnectionLost(
                reason="cli-failed",
                args=" ".join(args),
                stderr=err.decode(errors="replace")[-800:],
            )
        return out.decode(errors="replace")

    @staticmethod
    def _saved_path_from_stdout(stdout: str) -> Path | None:
        # CLI prints:  Saved /abs/path/Foo/Foo.mgd
        for line in stdout.splitlines():
            if line.startswith("Saved "):
                return Path(line.removeprefix("Saved ").strip())
        return None

    def _media_dir_for(self, mgd_path: Path) -> Path:
        return mgd_path.parent / f"{mgd_path.stem}_Media"

    async def _exec(self, *commands: str) -> str:
        """load -> command batch -> save, then pull the result back to
        self.project. saveProjectAs wraps outputs as <dir>/<Stem>/<Stem>.mgd,
        so trust the CLI's 'Saved ...' line rather than our requested path."""
        out_req = self.project.with_name(f"{self.project.stem}.mb-tmp.mgd")
        stdout = await self._run_cli(
            "exec", str(self.project), *commands, "--out", str(out_req)
        )
        saved = self._saved_path_from_stdout(stdout) or out_req
        shutil.copy2(saved, self.project)
        media = self._media_dir_for(saved)
        if media.is_dir():
            dst_media = self._media_dir_for(self.project)
            if dst_media.exists():
                shutil.rmtree(dst_media)
            shutil.move(media, dst_media)
        if saved != out_req and saved.parent.name == f"{out_req.stem}":
            shutil.rmtree(saved.parent, ignore_errors=True)
        elif out_req.exists() and out_req != saved:
            out_req.unlink()
        return stdout

    async def _dump_json(self) -> dict:
        from composerbench.errors import MalformedDump

        raw = await self._run_cli(
            "exec", str(self.project), "--dump-json",
            "--out", str(self.project),
        )
        # Engine chatter shares stdout with the dump and may itself contain
        # JSON fragments; accept only an object shaped like a project dump.
        decoder = json.JSONDecoder()
        pos = 0
        while True:
            brace = raw.find("{", pos)
            if brace == -1:
                break
            try:
                obj, end = decoder.raw_decode(raw[brace:])
            except json.JSONDecodeError:
                pos = brace + 1
                continue
            if isinstance(obj, dict) and "tracks" in obj:
                return obj
            pos = brace + max(end, 1)
        raise MalformedDump(detail="no project-shaped JSON in cli output", raw=raw[-400:])

    # ---- capability discovery ----------------------------------------------------
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            midi_edit=True,
            audio_clips=False,
            plugin_parameters=False,
            offline_render=True,
            section_render=True,
            automation=False,
            undo=False,
            snapshots=True,
        )

    # ---- queries -----------------------------------------------------------------
    async def get_project(self) -> ProjectInfo:
        d = await self._dump_json()
        proj = d.get("project", d)
        tracks = d.get("tracks", [])
        length_beats = max(
            (float(c.get("startBeat", 0)) + float(c.get("lengthBeats", 0))
             for t in tracks for c in t.get("clips", [])),
            default=0.0,
        )
        return ProjectInfo(
            title=proj.get("name", "Untitled"),
            tempo_bpm=float(proj.get("tempo", 120.0)),
            length_beats=length_beats,
            revision=int(proj.get("revision", 0)),
            tracks=[t.get("name", f"track:{t.get('id')}") for t in tracks],
        )

    async def get_tracks(self) -> list[Track]:
        d = await self._dump_json()
        return [
            Track(
                id=f"track:{t.get('id')}",
                name=t.get("name", ""),
                volume=float(t.get("volume", 1.0)),
                pan=float(t.get("pan", 0.0)),
                muted=bool(t.get("muted", False)),
            )
            for t in d.get("tracks", [])
        ]

    async def get_track(self, track_id: str) -> Track:
        tracks = await self.get_tracks()
        numeric = track_id.removeprefix("track:")
        for t in tracks:
            if t.id == track_id or t.name == numeric:
                return t
        matches = [t.name for t in tracks]
        if not numeric.isdigit():
            matches = [m for m in matches if numeric.lower() in m.lower()]
        raise EntityNotFound("track", track_id, matches)

    def _reverse_aliases(self) -> dict[int, str]:
        rev = {}
        for name, num in self._aliases.items():
            rev[num] = name.split(":", 1)[1] if name.startswith("clip@") else name.removeprefix("clip:")
        return rev

    async def get_clips(self, track_id: str | None = None) -> list[Clip]:
        d = await self._dump_json()
        rev = self._reverse_aliases()
        clips: list[Clip] = []
        wanted_numeric = track_id.removeprefix("track:") if track_id else None
        for t in d.get("tracks", []):
            tid = str(t.get("id"))
            if wanted_numeric is not None and wanted_numeric not in (tid, t.get("name")):
                continue
            for c in t.get("clips", []):
                cid_num = int(c.get("id"))
                # Project ComposerBench names over workstation names (§11):
                # agents always see the names they assigned.
                shown_name = rev.get(cid_num, c.get("name", ""))
                clip_start = float(c.get("startBeat", 0))
                clips.append(Clip(
                    id=f"clip:{cid_num}",
                    name=shown_name,
                    track_id=f"track:{tid}",
                    start=clip_start,
                    length=float(c.get("lengthBeats", 0)),
                    notes=[
                        # MAGDA stores note starts clip-relative; the
                        # ComposerBench contract is timeline-absolute.
                        Note(start=clip_start + float(n.get("startBeat", 0)),
                             duration=float(n.get("lengthBeats", 1)),
                             pitch=int(n.get("note", n.get("pitch", 60))),
                             velocity=int(n.get("velocity", 100)))
                        for n in c.get("notes", [])
                    ],
                ))
        return sorted(clips, key=lambda c: c.start)

    async def get_notes(self, track_id: str, clip_id: str | None = None) -> list[Note]:
        notes: list[Note] = []
        for clip in await self.get_clips(track_id):
            if clip_id and clip.id != clip_id and clip.name != clip_id.removeprefix("clip:"):
                continue
            notes.extend(clip.notes)
        return notes

    async def get_devices(self, track_id: str) -> list[Device]:
        """Devices ComposerBench loaded this session (registry-backed).

        The CLI dump exposes no device graph; full introspection needs the
        Level-3 OperationRegistry bridge (see docs/MAGDA_CAPABILITY_MAP.md).
        """
        tid = await self._numeric_track_id(track_id)
        return copy.deepcopy(self._track_devices.get(f"track:{tid}", []))

    async def get_parameters(self, track_id: str, device_id: str) -> dict:
        raise UnsupportedOperation(reason="parameter-query-not-in-cli-yet")

    # ---- edits (CLI dispatch commands) ----------------------------------------------
    async def create_track(self, name: str) -> Track:
        await self._exec("add-track", "audio", name)
        return await self._track_by_name(name)

    async def set_project_tempo(self, bpm: float) -> None:
        await self._exec("set-tempo", str(bpm))

    async def delete_track(self, track_id: str) -> None:
        await self._exec("delete-track", str(await self._numeric_track_id(track_id)))

    async def create_clip(self, track_id: str, name: str, start: float, length: float) -> Clip:
        tid = await self._numeric_track_id(track_id)
        before = {c.id for c in await self.get_clips(track_id)}
        await self._exec("add-midi-clip", str(tid), str(start), str(length))
        added = [c for c in await self.get_clips(track_id) if c.id not in before]
        if len(added) != 1:
            raise ValueError('cannot identify newly created clip unambiguously')
        created = added[0]
        self._remember(f"clip@{tid}:{name}", int(created.id.removeprefix("clip:")))
        return created.model_copy(update={"name": name})

    async def _numeric_clip_id(self, track_id: str, clip_ref: str) -> int:
        raw = clip_ref.removeprefix("clip:")
        clips = await self.get_clips(track_id)
        if raw.isdigit() and not any(c.id == f"clip:{raw}" for c in clips):
            seen = set()
            forwarded = int(raw)
            while forwarded in self._clip_remap and forwarded not in seen:
                seen.add(forwarded)
                forwarded = self._clip_remap[forwarded]
            raw = str(forwarded)
        matches = [c for c in clips if c.id == clip_ref or c.id == f"clip:{raw}" or c.name == raw]
        if len(matches) != 1:
            raise EntityNotFound('unambiguous clip on track', clip_ref, [c.name for c in clips])
        return int(matches[0].id.removeprefix('clip:'))

    async def _prepare_notes(self, track_id: str, clip_id: str, notes: list[Note]):
        """Resolve ids and convert timeline-absolute notes to clip-relative ones.

        The engine takes CLIP-RELATIVE starts and silently drops out-of-range
        notes while reporting success (verified experimentally), so
        containment is enforced here, before anything is sent.
        """
        from composerbench.errors import ParameterOutOfRange

        tid = await self._numeric_track_id(track_id)
        cid = await self._numeric_clip_id(track_id, clip_id)
        clips = await self.get_clips(track_id)
        clip = next(c for c in clips if int(c.id.removeprefix("clip:")) == cid)
        rel_notes = []
        for n in notes:
            rel = n.start - clip.start
            if rel < -1e-6 or rel + n.duration > clip.length + 1e-6:
                raise ParameterOutOfRange(
                    parameter="note.start",
                    received=n.start,
                    allowed=[clip.start, clip.start + clip.length],
                    hint=f"clip {clip.id!r} covers beats [{clip.start}, {clip.start + clip.length})",
                )
            rel_notes.append((max(0.0, rel), n))
        return tid, cid, clip, rel_notes

    async def add_notes(self, track_id: str, clip_id: str, notes: list[Note]) -> int:
        _, cid, _, rel_notes = await self._prepare_notes(track_id, clip_id, notes)
        cmds = []
        for rel, n in rel_notes:
            cmds.append("add-midi-note")
            cmds += [str(cid), f"{rel:.6f}", str(n.pitch), str(n.duration), str(n.velocity)]
        if cmds:
            await self._exec(*cmds)
        return len(notes)

    async def set_notes(self, track_id: str, clip_id: str, notes: list[Note]) -> int:
        """Replace a clip's notes. The CLI has no note delete, so the clip is
        rebuilt in place (same track/start/length, same ComposerBench name)."""
        tid, cid, clip, _ = await self._prepare_notes(track_id, clip_id, notes)
        before = {c.id for c in await self.get_clips(track_id)}
        await self._exec("delete-clip", str(cid), "add-midi-clip", str(tid),
                         repr(float(clip.start)), repr(float(clip.length)))
        added = [c for c in await self.get_clips(track_id)
                 if c.id not in before or c.id == f"clip:{cid}"]
        fresh = [c for c in added if abs(c.start - clip.start) < 1e-6]
        if len(fresh) != 1:
            raise ValueError("cannot identify the rebuilt clip unambiguously")
        new_id = int(fresh[0].id.removeprefix("clip:"))
        for key in [k for k, v in self._aliases.items() if v == cid]:
            del self._aliases[key]
        self._remember(f"clip@{tid}:{clip.name}", new_id)
        if new_id != cid:
            self._clip_remap[cid] = new_id
        return await self.add_notes(track_id, f"clip:{new_id}", notes) if notes else 0

    async def modify_notes(self, track_id: str, clip_id: str, note_indices: list[int],
                           *, transpose: int = 0, velocity_scale: float = 1.0,
                           time_scale: float = 1.0) -> int:
        if velocity_scale != 1.0 or time_scale != 1.0:
            raise UnsupportedOperation(
                reason="cli-supports-transpose-only",
                hint="use DSL-level bridge later; fake adapter covers full semantics",
            )
        await self._exec(
            "transpose-midi-clip",
            str(await self._numeric_clip_id(track_id, clip_id)),
            str(transpose),
        )
        return -1  # CLI transposes whole clip; per-note selection comes via C++ api later

    async def delete_notes(self, track_id: str, clip_id: str, note_indices: list[int]) -> int:
        raise UnsupportedOperation(reason="note-delete-not-in-cli-dispatcher-yet")

    async def set_track_volume(self, track_id: str, volume: float) -> None:
        tid = await self._numeric_track_id(track_id)
        await self._exec(
            "set-track-volume", str(tid), f"{max(0.0, min(2.0, float(volume)))}",
        )

    async def load_device(self, track_id: str, device_name: str) -> Device:
        self._guard_instrument(device_name)
        tid = await self._numeric_track_id(track_id)
        await self._exec(
            "add-internal-instrument", str(tid), device_name,
        )
        device = Device(id=f"device:{device_name}", name=device_name, type="instrument")
        key = f"track:{tid}"
        loaded = self._track_devices.setdefault(key, [])
        if device.name not in [d.name for d in loaded]:
            loaded.append(device)
        self._save_device_registry()
        return copy.deepcopy(device)

    def _guard_instrument(self, device_name: str) -> None:
        """Refuse instrument ids known (or believed) to render silence.

        The MAGDA CLI accepts any string for add-internal-instrument and
        returns success even when the id matches nothing; the resulting
        project renders -120 dBFS. This guard fails fast instead, using the
        freshest source of truth available (doctor manifest, else built-in
        allowlist). Set COMPOSERBENCH_ALLOW_ANY_INSTRUMENT=1 to bypass while
        probing experimental binaries.
        """
        if allow_any_instrument():
            return
        audible, source = audible_instruments_for(self.project.parent)
        requested = device_name.strip().lower()
        if audible and requested not in audible:
            raise UnsupportedOperation(
                reason="instrument-not-in-audible-registry",
                requested=device_name,
                registry_source=source,
                hint="run `composerbench doctor` to probe this binary, or set "
                     "COMPOSERBENCH_ALLOW_ANY_INSTRUMENT=1 to override",
                known=sorted(audible)[:12],
            )

    def _device_registry_file(self) -> Path:
        return self.project.parent / ".musicbench" / "devices.json"

    def _save_device_registry(self) -> None:
        import json

        path = self._device_registry_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {k: [d.model_dump() for d in v] for k, v in self._track_devices.items()},
            indent=1,
        ))

    def _load_device_registry(self) -> None:
        import json

        path = self._device_registry_file()
        if not path.exists():
            return
        data = json.loads(path.read_text())
        self._track_devices = {
            k: [Device.model_validate(d) for d in v] for k, v in data.items()
        }

    async def set_parameter(self, track_id: str, device_id: str, parameter: str, value: float) -> None:
        raise UnsupportedOperation(reason="parameter-set-not-in-cli-dispatcher-yet")

    async def automate_parameter(self, track_id: str, device_id: str, parameter: str,
                                 points: list[tuple[float, float]]) -> None:
        raise UnsupportedOperation(reason="automation-not-in-cli-yet")

    # ---- render / safety --------------------------------------------------------------
    async def render(self, request: RenderRequest) -> Path:
        if request.track_ids is not None:
            raise UnsupportedOperation(reason='isolated-track-render-not-supported; refusing to return a full mix as a stem')
        out = Path(request.output_path)
        args = ["render", str(self.project), "--wav", str(out), "--bit-depth", str(request.bit_depth)]
        if request.start_beats is not None:
            args += ["--from", f"{request.start_beats}beats"]
        if request.length_beats is not None:
            args += ["--to", f"{(request.start_beats or 0) + request.length_beats}beats"]
        await self._run_cli(*args)
        if not out.exists():
            from composerbench.errors import RenderFailed
            raise RenderFailed(output=str(out))
        return out

    async def save_project(self) -> None:
        return  # every exec already persists

    async def snapshot(self, label: str) -> str:
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)[:60] or "snapshot"
        snap_dir = self._snapshot_dir / f"{safe_label}-{uuid.uuid4().hex}"
        snap_dir.mkdir()
        shutil.copy2(self.project, snap_dir / self.project.name)
        if self._alias_file.exists():
            shutil.copy2(self._alias_file, snap_dir / "aliases.json")
        media = self._media_dir_for(self.project)
        if media.is_dir():
            shutil.copytree(media, self._media_dir_for(snap_dir / self.project.name))
        from composerbench.transactions import capture_sidecar
        capture_sidecar(self.project.parent / ".musicbench", snap_dir / "sidecar")
        return str(snap_dir / self.project.name)

    async def restore_snapshot(self, snapshot_id: str) -> None:
        import json

        snap = Path(snapshot_id)
        if not snap.exists():
            raise EntityNotFound("snapshot", snapshot_id)
        shutil.copy2(snap, self.project)
        from composerbench.transactions import restore_sidecar
        root = self.project.parent / ".musicbench"
        if (snap.parent / "sidecar").exists():
            restore_sidecar(root, snap.parent / "sidecar")
        else:  # snapshots from before the complete-sidecar format
            aliases = snap.parent / "aliases.json"
            if aliases.exists():
                shutil.copy2(aliases, self._alias_file)
        self._aliases = {}
        self._track_devices = {}
        self._load_aliases()
        self._load_device_registry()
        snap_media = self._media_dir_for(snap)
        dst = self._media_dir_for(self.project)
        if dst.exists():
            shutil.rmtree(dst)
        if snap_media.is_dir():
            shutil.copytree(snap_media, dst)

    async def undo(self) -> None:
        raise UnsupportedOperation(reason="undo-not-exposed-by-cli")

    async def redo(self) -> None:
        raise UnsupportedOperation(reason="undo-not-exposed-by-cli")

    # ---- helpers ----------------------------------------------------------------------
    async def _numeric_track_id(self, track_id: str) -> int:
        raw = track_id.removeprefix("track:")
        if raw.isdigit():
            return int(raw)
        d = await self._dump_json()
        for t in d.get("tracks", []):
            if t.get("name") == raw:
                return int(t["id"])
        raise EntityNotFound("track", track_id, [t.get("name") for t in d.get("tracks", [])])

    async def _track_by_name(self, name: str) -> Track:
        for t in await self.get_tracks():
            if t.name == name:
                return t
        raise EntityNotFound("track", name)
