"""Persistent-engine transport for MAGDA (spec §113 Level 3).

`magda_cli serve <project.mgd> [--sock path]` boots the headless engine ONCE
and answers JSON-line requests over a Unix socket:

    {"op":"ping"}
    {"op":"exec","commands":[...]}        dispatch + save
    {"op":"dump"}                         full project JSON (single line)
    {"op":"render","wav":"/abs/out.wav","from":"8beats","to":null}
    {"op":"save"}
    {"op":"shutdown"}

Every response is one JSON line. Control ops answer an envelope
({"ok":true,...} / {"ok":false,"error":...}); `dump` answers with the raw
project document itself.

Protocol 2 (probed with {"op":"describe"}; every addition is optional and the
adapter falls back to protocol-1 behaviour against older binaries):

    {"op":"describe"}                     -> {"ok":true,"protocol":2,"features":{...}}
    {"op":"exec","commands":[...],"save":false}   dispatch without saving
    {"op":"notes","clipId":N,"mode":"add"|"replace","notes":[{start,length,pitch,velocity}]}
                                          one undo step, one engine sync per clip
    {"op":"param_info","deviceId":N,"param":name|index}  choices + display texts
    dump devices[].parameters[] may carry choices/text/steps/default
    add_plugin hosts the plugin inside MAGDA's device model, so it (and its
    state) is saved in the .mgd and listed in `dump` like any other device

MagdaServeAdapter speaks this protocol. It subclasses MagdaCliAdapter and
inherits snapshots/aliases/guards; only transport-sensitive methods are
overridden. The win: one engine boot per session instead of one per command,
which turns long composition pipelines from minutes of boot overhead into
milliseconds of dispatch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda import MagdaCliAdapter
from composerbench.errors import (
    ComposerBenchError,
    ConnectionLost,
    EntityNotFound,
    RenderFailed,
)
from composerbench.model import Note
from composerbench.params import (
    UnknownParameterLabel,
    annotate_host,
    choices_of,
    label_for,
    mark_host_wrapper,
    resolve_value,
)
from composerbench.params import describe as describe_param
from composerbench.params import find as find_param
from composerbench.params import match as match_param

log = logging.getLogger(__name__)


class ServeCommandFailed(ComposerBenchError):
    code = "SERVE_COMMAND_FAILED"


class AddFxFailed(ComposerBenchError):
    code = "ADD_FX_FAILED"


def with_suppressed_kill(proc: asyncio.subprocess.Process) -> None:
    """Kill a booting daemon, tolerating an already-exited process."""
    try:
        proc.kill()
    except ProcessLookupError:
        pass                        # already exited
    except OSError as e:
        log.debug("could not kill engine %s: %s", proc.pid, e)


# Tracktion built-ins that `add-fx` accepts alongside magda_* ids (probe-verified).
TRACKTION_BUILTIN_FX = frozenset({"reverb", "delay", "chorus", "phaser", "compressor",
                                  "4bandEq", "lowpass", "pitchShifter"})


def _is_internal_fx(plugin_id: str) -> bool:
    return plugin_id.startswith("magda_") or plugin_id in TRACKTION_BUILTIN_FX


# Requests that never change project state; everything else invalidates caches.
READ_ONLY_OPS = frozenset({"ping", "dump", "plugins", "ext_params", "describe",
                           "param_info", "render", "render_stems", "catalog", "save"})


class MagdaServeAdapter(MagdaCliAdapter):
    """Drop-in upgrade of MagdaCliAdapter backed by a persistent engine.

        daw = MagdaServeAdapter(cli, project)
        await daw.initialize()      # launches the serve daemon
        ... ordinary DawAdapter usage; commands ride the socket ...
        await daw.shutdown()        # stops the daemon

    If the daemon dies mid-session, operations raise ConnectionLost; call
    initialize() again to relaunch (state reloads from the saved .mgd).
    """

    def __init__(self, cli_path: Path | str, project_path: Path | str,
                 serve: bool = True, sock_path: Path | str | None = None):
        super().__init__(cli_path, project_path)
        self._serve_enabled = serve
        self._serve_proc: asyncio.subprocess.Process | None = None
        self._sock_path: Path | None = Path(sock_path) if sock_path else None
        self._log_drainers = []
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        # Protocol-2 state
        self.autosave = True              # exec saves after each batch unless told otherwise
        self._features: dict | None = None
        self._describe: dict = {}
        self._dump_cache: dict | None = None
        self._saved_meta: dict | None = None   # device id -> param name -> extras from the .mgd
        # Protocol-1 engines host plugins outside the project model: they are
        # absent from `dump` and from the saved .mgd. Track them here so they
        # still show up as devices (flagged persistent=False) this session.
        self._ext_devices: dict[int, dict] = {}
        self._plugin_cache: list[dict] | None = None

    def capabilities(self):
        from dataclasses import replace
        f = self._features or {}
        return replace(super().capabilities(), plugin_parameters=True, effects=True,
                       semantic_roles=True, external_plugins=True,
                       bulk_notes=True,                       # native op or coalesced exec
                       explicit_save=bool(f.get("execSaveFlag")),
                       parameter_labels=True,                 # dump labels or saved-project fallback
                       plugin_persistence=bool(f.get("pluginPersistence")))

    def _feature(self, name: str) -> bool:
        return bool((self._features or {}).get(name))

    async def features(self) -> dict:
        """Server protocol features (starts the engine if needed)."""
        await self._ensure_session()
        return {"protocol": self._describe.get("protocol", 1), **(self._features or {})}

    async def _probe_features(self) -> None:
        if self._features is not None:
            return
        self._features = {}
        try:
            reply = await self._send_raw({"op": "describe"})
        except (ConnectionLost, OSError):
            raise
        except Exception:  # noqa: BLE001 - older servers may answer oddly
            reply = None
        if isinstance(reply, dict) and reply.get("ok") and isinstance(reply.get("features"), dict):
            self._describe = reply
            self._features = dict(reply["features"])
        else:
            self._describe = {"protocol": 1}

    async def snapshot(self, label: str) -> str:
        await self._ensure_session()
        if self._serve_proc is None:
            from composerbench.errors import UnsupportedOperation
            raise UnsupportedOperation(reason='transactions require an owned persistent session',
                                       hint='connect without MAGDA_SOCK to use a dedicated engine')
        await self.save_project()
        return await super().snapshot(label)

    async def restore_snapshot(self, snapshot_id: str) -> None:
        if self._writer is not None and self._serve_proc is None:
            from composerbench.errors import UnsupportedOperation
            raise UnsupportedOperation(reason='cannot restore through an externally owned engine')
        # Stop before replacing files; the old engine must never overwrite them.
        await self.shutdown()
        await super().restore_snapshot(snapshot_id)
        self._dump_cache = None
        self._saved_meta = None
        await self._ensure_session()

    async def get_devices(self, track_id: str):
        from composerbench.model import Device
        return [Device(id=str(d["id"]), name=d.get("name", str(d["id"])),
                       type=d.get("type", "unknown"), format=d.get("format", "internal"),
                       parameters={str(p["index"]): p for p in d.get("parameters", [])})
                for d in await self._device_entries(track_id)]

    # ------------------------------------------------------------ lifecycle --
    async def _ensure_session(self) -> None:
        if self._writer is not None:
            return
        # Attach to an already-running daemon (e.g. one started by the
        # `composerbench serve` CLI verb) instead of booting a fresh engine.
        env_sock = os.environ.get("MAGDA_SOCK")
        if env_sock and self._sock_path is None:
            self._sock_path = Path(env_sock)
        if self._sock_path is not None and self._sock_path.exists():
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    str(self._sock_path), limit=16 * 1024 * 1024)
                await self._request({"op": "ping"})
                await self._probe_features()
                return
            except (ConnectionError, FileNotFoundError, OSError):
                self._reader = self._writer = None  # stale socket; fall through
        sock = self._sock_path or self._default_sock_path()
        proc = await asyncio.create_subprocess_exec(
            str(self.cli), "serve", str(self.project), "--sock", str(sock),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._serve_proc = proc
        # The engine chatters on stdout before our banner appears; scan until
        # we see it (or the process dies / times out).
        banner_ok = False
        chatter: list[str] = []
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 90
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                line_bytes = await asyncio.wait_for(proc.stdout.readline(),
                                                    timeout=remaining)
                if not line_bytes:  # EOF: daemon died during boot
                    err = await proc.stderr.read(4000) if proc.stderr else b""
                    raise ConnectionLost(
                        reason="serve-boot-failed",
                        stderr=err.decode(errors="replace")[-800:]
                        or "\n".join(chatter)[-800:],
                    )
                text = line_bytes.decode(errors="replace").strip()
                if text.startswith("SERVING"):
                    banner_ok = True
                    break
                if text:
                    chatter.append(text)
                    # Keep the buffer small; boot logs can be long.
                    if len(chatter) > 50:
                        chatter.pop(0)
        except TimeoutError as e:
            await self._drop_session()
            raise ConnectionLost(reason="serve-boot-timeout") from e
        except BaseException:
            await self._drop_session()
            raise
        if not banner_ok:
            with_suppressed_kill(proc)
            raise ConnectionLost(reason="serve-boot-failed",
                                 stderr="\n".join(chatter)[-800:])
        self._serve_proc = proc
        async def drain(stream):
            while await stream.read(65536):
                pass
        self._log_drainers = [asyncio.create_task(drain(stream)) for stream in (proc.stdout, proc.stderr)]
        self._sock_path = sock
        # Project dumps arrive as ONE long JSON line; with hundreds of notes
        # plus per-device parameter metadata they blow past the 64 KB
        # StreamReader default. Allow 16 MB lines.
        self._reader, self._writer = await asyncio.open_unix_connection(
            str(sock), limit=16 * 1024 * 1024)
        self._dump_cache = None
        await self._probe_features()

    def _default_sock_path(self) -> Path:
        """Short socket path — macOS AF_UNIX is capped ~104 chars, and pytest
        tmp paths blow past that. Stage under a hash in the temp dir."""
        raw = str(self.project.parent / f"{self.project.stem}.sock")
        if len(raw) > 90:
            import hashlib
            h = hashlib.sha1(raw.encode()).hexdigest()[:12]
            return Path("/tmp") / f"mb-{h}.sock"
        return Path(raw)

    async def shutdown(self) -> None:
        """Politely stop the serve daemon."""
        if self._writer is not None:
            try:
                self._writer.write(b'{"op":"shutdown"}\n')
                await self._writer.drain()
                await asyncio.wait_for(self._reader.readline(), timeout=10)
            except (TimeoutError, OSError, ConnectionError) as e:
                log.debug("engine did not acknowledge shutdown: %s", e)   # killed below
        await self._drop_session()

    async def _drop_session(self) -> None:
        if self._serve_proc is not None:
            # Our engine is going away and takes out-of-model plugins with it.
            self._ext_devices.clear()
        if self._writer is not None:
            try:
                self._writer.close()
            except (OSError, RuntimeError) as e:
                log.debug("closing the engine socket failed: %s", e)
        self._writer = self._reader = None
        if self._serve_proc is not None and self._serve_proc.returncode is None:
            self._serve_proc.kill()
        if self._serve_proc is not None:
            await self._serve_proc.wait()
        self._serve_proc = None
        for task in self._log_drainers:
            task.cancel()
        await asyncio.gather(*self._log_drainers, return_exceptions=True)
        self._log_drainers = []

    # -------------------------------------------------------------- protocol --
    async def _send_raw(self, payload: dict) -> dict:
        """Send one request, return the parsed response document (no ok-check).

        Responses may be single-line OR pretty-printed multi-line JSON
        (JUCE formatting varies by call site), so accumulate bytes until the
        buffer parses as a complete JSON value.
        """
        await self._ensure_session()
        if payload.get("op") not in READ_ONLY_OPS:
            self._dump_cache = None
        try:
            self._writer.write((json.dumps(payload) + "\n").encode())
            await self._writer.drain()
            deadline = asyncio.get_running_loop().time() + 900
            buf = b""
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise ConnectionLost(reason="serve-request-timeout")
                chunk = await asyncio.wait_for(self._reader.read(65536),
                                               timeout=remaining)
                if not chunk:
                    await self._drop_session()
                    raise ConnectionLost(reason="serve-session-ended")
                buf += chunk
                # Complete when the buffer ends with a newline AND parses.
                if buf.endswith(b"\n"):
                    try:
                        return json.loads(buf.decode(errors="replace"))
                    except json.JSONDecodeError:
                        continue  # partial pretty-printed doc; keep reading
        except asyncio.CancelledError:
            await self._drop_session()
            raise
        except (ConnectionResetError, BrokenPipeError) as e:
            await self._drop_session()
            raise ConnectionLost(reason="serve-connection-lost") from e
        except TimeoutError as e:
            await self._drop_session()
            raise ConnectionLost(reason="serve-request-timeout") from e

    async def _request(self, payload: dict) -> dict:
        """Envelope-checked request: raises on {ok:false}."""
        reply = await self._send_raw(payload)
        if isinstance(reply, dict) and not reply.get("ok", False):
            raise ServeCommandFailed(error=str(reply.get("error", "?")))
        return reply

    # ------------------------------------------------- overridden transports --
    async def _exec(self, *commands: str, save: bool | None = None) -> str:
        """Batched edit via the persistent engine (raises on any failure).

        With protocol 2 the per-request save can be skipped (``autosave`` or
        ``save=False``); call ``save_project()`` once at the end instead.
        """
        payload: dict = {"op": "exec", "commands": list(commands)}
        if self._features is None:
            await self._ensure_session()
        if self._feature("execSaveFlag"):
            payload["save"] = self.autosave if save is None else bool(save)
        await self._request(payload)
        return ""

    async def _dump_json(self) -> dict:
        """The dump response line IS the project document.

        Cached until the next mutating request when this adapter owns the
        engine (nobody else can change it). Treat the result as read-only.
        """
        if self._dump_cache is not None and self._serve_proc is not None:
            return self._dump_cache
        reply = await self._send_raw({"op": "dump"})
        if not isinstance(reply, dict) or "tracks" not in reply:
            raise ConnectionLost(reason="serve-dump-malformed",
                                 got=str(reply)[:120])
        if self._serve_proc is not None:
            self._dump_cache = reply
        return reply

    # ------------------------------------------------------------ bulk notes --
    async def add_notes(self, track_id: str, clip_id: str, notes: list[Note]) -> int:
        """All notes in one request: a single undo step and engine sync."""
        if not notes:
            return 0
        _, cid, _, rel_notes = await self._prepare_notes(track_id, clip_id, notes)
        if self._feature("bulkNotes"):
            await self._request({"op": "notes", "clipId": cid, "mode": "add",
                                 "notes": self._notes_payload(rel_notes),
                                 "save": self.autosave})
            return len(notes)
        return await super().add_notes(track_id, clip_id, notes)

    async def set_notes(self, track_id: str, clip_id: str, notes: list[Note]) -> int:
        """Replace a clip's notes in place (clip id and name are kept)."""
        if not self._feature("bulkNotes"):
            return await super().set_notes(track_id, clip_id, notes)
        _, cid, _, rel_notes = await self._prepare_notes(track_id, clip_id, notes)
        await self._request({"op": "notes", "clipId": cid, "mode": "replace",
                             "notes": self._notes_payload(rel_notes),
                             "save": self.autosave})
        return len(notes)

    @staticmethod
    def _notes_payload(rel_notes) -> list[dict]:
        return [{"start": round(rel, 6), "length": float(n.duration),
                 "pitch": int(n.pitch), "velocity": int(n.velocity)} for rel, n in rel_notes]

    async def supports_isolated_render(self) -> bool:
        await self._ensure_session()
        return self._feature("stems")

    async def render(self, request: RenderRequest) -> Path:
        out = Path(request.output_path)
        payload: dict = {"op": "render", "wav": str(out),
                         "bitDepth": request.bit_depth}
        if request.track_ids is not None:
            if not await self.supports_isolated_render():
                from composerbench.errors import UnsupportedOperation
                raise UnsupportedOperation(
                    reason='isolated-track-render-not-supported; refusing to return a full mix as a stem',
                    hint='needs MAGDA serve protocol 3 (the "stems" feature)')
            if not request.track_ids:
                # the engine reads an empty list as "every track": never return a mix as a stem
                raise ValueError("render with an empty track list")
            payload["tracks"] = [await self._numeric_track_id(t) for t in request.track_ids]
        if request.start_beats is not None:
            payload["from"] = f"{request.start_beats}beats"
        if request.length_beats is not None:
            payload["to"] = f"{(request.start_beats or 0) + request.length_beats}beats"
        await self._request(payload)
        if not out.exists():
            raise RenderFailed(output=str(out))
        return out

    # ------------------------------------------------------------- protocol 3 --
    # Engine-side mixing: channels, sends, sidechain keys, automation lanes,
    # arrangement edits, master inserts and one-pass stem renders. Each method
    # checks the engine's feature flag and fails with a clear hint on older
    # binaries instead of sending an op the engine does not know.

    async def _need(self, feature: str, what: str) -> None:
        await self._ensure_session()
        if not self._feature(feature):
            from composerbench.errors import UnsupportedOperation
            raise UnsupportedOperation(
                reason=f"{what} needs MAGDA serve protocol 3 (feature {feature!r})",
                hint="build magda_cli with the ComposerBench engine patch (docs/BUILDING_MAGDA.md)")

    async def _channel(self, track: str | int) -> int | str:
        if str(track).lower() == "master":
            return "master"
        return int(track) if isinstance(track, int) else await self._numeric_track_id(str(track))

    @staticmethod
    def _channel_of(reply: dict) -> dict:
        """The channel document of a mixer/send reply (id, volume, volumeDb, pan, sends...)."""
        return reply.get("track") or reply.get("master") or reply

    @staticmethod
    def _clean(payload: dict) -> dict:
        return {k: v for k, v in payload.items() if v is not None}

    async def set_mixer(self, track: str | int, *, volume: float | None = None, volume_db: float | None = None,
                        pan: float | None = None, mute: bool | None = None, solo: bool | None = None,
                        name: str | None = None) -> dict:
        """Channel settings for a track or "master" (volume linear 0..2 or in dB, pan -1..1).
        Returns the channel: id, name, volume, volumeDb, pan, muted, soloed, sends."""
        await self._need("mixer", "mixer")
        return self._channel_of(await self._request(self._clean({
            "op": "mixer", "trackId": await self._channel(track), "volume": volume, "volumeDb": volume_db,
            "pan": pan, "mute": mute, "solo": solo, "name": name, "save": self.autosave})))

    async def set_send(self, source: str | int, dest: str | int, *, level_db: float | None = None,
                       level: float | None = None, remove: bool = False) -> dict:
        """Post-fader send from one track to an aux/group track (remove=True deletes it)."""
        await self._need("sends", "sends")
        return self._channel_of(await self._request(self._clean({
            "op": "send", "from": await self._channel(source), "to": await self._channel(dest),
            "levelDb": level_db, "level": level, "remove": remove or None, "save": self.autosave})))

    async def add_effect(self, track: str | int, plugin: str, *, position: str = "pre",
                         name: str | None = None) -> int:
        """Insert a built-in effect on a track or on the master bus; returns its device id.
        position "post" puts it after the fader (cannot be sidechained or automated)."""
        await self._need("masterBus", "add_fx")
        reply = await self._request(self._clean({
            "op": "add_fx", "trackId": await self._channel(track), "plugin": plugin, "name": name,
            "position": position, "save": self.autosave}))
        return int(reply["device"])

    async def set_sidechain(self, device_id: int, source: str | int | None, kind: str = "midi") -> dict:
        """Key a pre-fader device (e.g. the "sidechain" ducker or a compressor) from another track;
        source=None clears it. kind "midi" keys from notes, "audio" from the source's audio."""
        await self._need("sidechain", "sidechain")
        src = None if source is None else await self._channel(source)
        return await self._request({"op": "sidechain", "deviceId": int(device_id), "source": src,
                                    "type": kind, "save": self.autosave})

    async def write_automation(self, target: dict, points: list[dict], mode: str = "replace",
                               unit: str | None = None) -> dict:
        """Write an automation lane in real units.

        target: {"kind": "volume"|"pan", "track": ...}, {"kind": "send", "track": ..., "to": ...}
        or {"kind": "param", "device": id, "param": name|index}. points: [{"beat", "value",
        "curve"?}] - volume in dB, pan -1..1, parameters in their native range or as labels.
        mode: replace | add | range | clear.
        """
        await self._need("automation", "automation")
        tgt = {"kind": target["kind"]}
        if "track" in target:
            tgt["trackId"] = await self._channel(target["track"])
        if "to" in target:
            tgt["to"] = await self._channel(target["to"])
        if "device" in target:
            tgt["deviceId"] = int(target["device"])
        if "param" in target:
            tgt["param"] = target["param"]
        return await self._request(self._clean({"op": "automation", "target": tgt, "points": points,
                                                "mode": mode, "unit": unit, "save": self.autosave}))

    async def clip_edit(self, action: str, *, clip: str | int | None = None, track: str | int | None = None,
                        **fields) -> dict:
        """Arrangement edits at absolute beats: create, move, resize, duplicate, rename, delete, loop.

        fields are passed through (start, length, name, loopLength, overlap="error"|"replace"|"shift").
        """
        await self._need("clipOps", "clip edits")
        payload = {"op": "clip", "action": action, "save": self.autosave, **fields}
        if clip is not None:
            payload["clipId"] = int(str(clip).removeprefix("clip:"))
        if track is not None:
            payload["trackId"] = await self._channel(track)
        reply = await self._request(payload)
        return reply.get("clip", reply)

    async def render_stems(self, out_dir: Path | str, track_ids: list[str] | None = None,
                           start_beats: float | None = None, length_beats: float | None = None,
                           bit_depth: int = 24) -> list[dict]:
        """One WAV per channel (default: every channel feeding the master) in out_dir."""
        await self._need("stems", "render_stems")
        payload: dict = {"op": "render_stems", "dir": str(Path(out_dir).resolve()), "bitDepth": bit_depth}
        if track_ids is not None:
            payload["tracks"] = [await self._numeric_track_id(t) for t in track_ids]
        if start_beats is not None:
            payload["from"] = f"{start_beats}beats"
        if length_beats is not None:
            payload["to"] = f"{(start_beats or 0) + length_beats}beats"
        return (await self._request(payload))["stems"]

    async def engine_catalog(self) -> dict:
        """Built-in instruments and effects as the running engine lists them."""
        await self._need("catalog", "catalog")
        reply = await self._request({"op": "catalog"})
        return {k: reply[k] for k in ("instruments", "effects", "externalPlugins") if k in reply}

    async def save_project(self) -> None:
        await self._request({"op": "save"})

    # --------------------------------------------- new capabilities (FX/params)--
    async def _device_entries(self, track_id: str) -> list[dict]:
        import copy
        tid = await self._numeric_track_id(track_id)
        data = await self._dump_json()
        entries = [copy.deepcopy(d) for d in data.get("devices", []) if d.get("trackId") == tid]
        entries += [copy.deepcopy(d) for d in self._ext_devices.values() if d["trackId"] == tid]
        return entries

    async def add_fx(self, track_id: str, plugin_id: str,
                     as_instrument: bool = False) -> int:
        """Attach an effect to the track's chain; returns its device id.

        Accepts internal effect ids (magda_reverb...) OR external plugin
        ids/names (e.g. "VST3-Dragonfly Room Reverb-..."). Externals route
        through add_plugin because the internal add-fx path only knows the
        compiled registry. Returns the numeric/ext device handle usable with
        set_parameter / set_external_parameter.
        """
        if not _is_internal_fx(str(plugin_id)):
            return await self.add_plugin(track_id, plugin_id,
                                         as_instrument=as_instrument)
        tid = await self._numeric_track_id(track_id)
        await self._exec("add-fx", str(tid), plugin_id)
        # NOTE: instrument ids and fx ids come from separate counters, so a
        # before/after id-diff can miss the new device entirely. The fx keeps
        # its plugin id as its display name (no custom name passed), so
        # match on that instead.
        for dev in reversed(await self._device_entries(track_id)):
            if dev.get("name") == plugin_id:
                return int(dev["id"])
        raise AddFxFailed(plugin=plugin_id,
                          track=track_id,
                          hint="fx command succeeded but no matching device appeared")

    async def get_devices_full(self, track_id: str) -> list[dict]:
        """Full device projection incl. post-fx and parameter metadata."""
        return await self._device_entries(track_id)

    def _is_outside_model(self, dev: dict) -> bool:
        """Hosted beside the project model (protocol 1): parameters come
        from ext_params and are set with set_ext_param."""
        try:
            if int(dev.get('id')) in self._ext_devices:
                return True
        except (TypeError, ValueError):
            pass
        return dev.get('format', 'internal').lower() != 'internal' and not dev.get('parameters')

    async def _resolve_device(self, track_id, device_ref):
        entries = await self._device_entries(track_id)
        want = str(device_ref).removeprefix('device:')
        matches = [d for d in entries if str(d['id']) == want or d.get('name') == want]
        if len(matches) != 1:
            raise EntityNotFound('unambiguous device', str(device_ref), [str(d['id']) for d in entries])
        return matches[0]

    async def get_parameters(self, track_id: str, device_ref: str | int) -> dict:
        """Live values, bounds and *meaning* of every parameter on a device.

        Each entry: name, stableId, value, min, max, unit and, when known,
        choices (discrete labels), text (display text of the current value),
        steps and default. Older MAGDA binaries omit labels from the live
        dump; they are then read back from the saved project, which keeps them.
        """
        dev = await self._resolve_device(track_id, device_ref)
        if self._is_outside_model(dev):
            raw = [{'name': p['name'], 'stableId': p['name'], 'value': p['value'],
                    'min': 0.0, 'max': 1.0, 'unit': 'normalized',
                    **{k: p[k] for k in ('text', 'label', 'steps', 'choices', 'choiceValues', 'default')
                       if p.get(k) not in (None, '', [])}}
                   for p in await self.external_parameters(int(dev['id']))]
            return dict(enumerate(mark_host_wrapper(raw)))
        extras = await self._param_extras(dev)
        out = {}
        for p in dev.get('parameters', []):
            meta = {'name': p.get('name', ''), 'stableId': p.get('stableId') or p.get('name', ''),
                    'value': p.get('value', 0.0), 'min': p.get('min', 0.0),
                    'max': p.get('max', 1.0), 'unit': p.get('unit') or ''}
            for key in ('choices', 'choiceValues', 'text', 'steps', 'default', 'scale', 'owner', 'wrapperRole'):
                if p.get(key) not in (None, '', []):
                    meta[key] = p[key]
            extra = extras.get(meta['name'], {})
            for key in ('choices', 'default'):
                if key not in meta and extra.get(key) not in (None, []):
                    meta[key] = extra[key]
            if 'text' not in meta:
                meaning = label_for(meta)
                if meaning is not None:
                    meta['text'] = meaning
            out[p['index']] = annotate_host(meta)
        return out

    async def _param_extras(self, dev: dict) -> dict:
        """Choice labels/defaults for a device from the saved .mgd (fallback)."""
        params = dev.get('parameters') or []
        # Older live dumps carry `"choices": null` even for discrete
        # parameters; the saved project keeps the labels.
        if self._feature('paramLabels') or not params or all(p.get('choices') for p in params):
            return {}
        key = str(dev.get('id'))
        if self._saved_meta is None or key not in self._saved_meta:
            await self.save_project()
            self._saved_meta = self._read_saved_param_meta()
        return (self._saved_meta or {}).get(key, {})

    def _read_saved_param_meta(self) -> dict:
        import zlib
        try:
            raw = self.project.read_bytes()
            try:
                doc = json.loads(zlib.decompress(raw))
            except zlib.error:
                doc = json.loads(raw)
        except (OSError, ValueError):
            return {}
        out: dict = {}
        for track in doc.get('tracks', []):
            for slot in ('chainElements', 'postFxChainElements'):
                for element in track.get(slot, []) or []:
                    device = element.get('device', element) if isinstance(element, dict) else {}
                    if not isinstance(device, dict) or 'parameters' not in device:
                        continue
                    table = out.setdefault(str(device.get('id')), {})
                    for p in device.get('parameters', []):
                        entry = table.setdefault(p.get('name', ''), {})
                        if p.get('choices'):
                            entry['choices'] = list(p['choices'])
                        if 'defaultValue' in p:
                            entry['default'] = p['defaultValue']
        return out

    async def set_parameter(self, track_id: str, device_ref: str | int,
                            parameter: str | int, value: float | str) -> None:
        """Set by index, name or stable id; ``value`` may be a number or a
        choice label such as "Hall", "1/8." or "LP 12 dB"."""
        dev = await self._resolve_device(track_id, device_ref)
        params = await self.get_parameters(track_id, device_ref)
        key, meta = find_param(params, parameter)
        try:
            native = resolve_value(meta, value)
        except UnknownParameterLabel:
            # Hosted plugins may only reveal their labels on request.
            info = await self._param_info(dev, key, meta, params)
            if not info.get('choices'):
                raise
            native = resolve_value({**meta, **info}, value)
        if self._is_outside_model(dev):
            await self.set_external_parameter(int(dev['id']), meta['name'], native,
                                              index=self._ext_index(params, key, meta))
            return
        await self._request({'op': 'exec', 'commands': [
            'set-device-parameter', str(dev['id']), self._param_ref(params, key, meta),
            repr(float(native))],
            **({'save': self.autosave} if self._feature('execSaveFlag') else {})})

    async def _native_value(self, dev: dict, key, meta: dict, params: dict, requested) -> tuple[float, str]:
        """(native value, how) for a number, a label or {"label", "value"}.

        Labels resolve against the live metadata (asking the engine for a hosted
        plugin's labels when needed); ``{"label", "value"}`` falls back to the
        number when the label cannot be resolved; numbers are clamped into the
        parameter's range instead of rejected.
        """
        if isinstance(requested, dict):
            label, number = requested.get("label"), requested.get("value")
            if label is not None:
                try:
                    native, _ = await self._native_value(dev, key, meta, params, str(label))
                    return native, "label"
                except (UnknownParameterLabel, ValueError):
                    if number is None:
                        raise
            native, how = await self._native_value(dev, key, meta, params, float(number))
            return native, "value-fallback" if label is not None else how
        if isinstance(requested, str):
            try:
                float(requested)
            except ValueError:
                try:
                    return resolve_value(meta, requested), "label"
                except UnknownParameterLabel:
                    info = await self._param_info(dev, key, meta, params)
                    if not info.get('choices'):
                        raise
                    return resolve_value({**meta, **info}, requested), "label"
            requested = float(requested)
        value = float(requested)
        lo, hi = float(meta.get('min', 0.0)), float(meta.get('max', 1.0))
        clamped = min(hi, max(lo, value))
        return clamped, "value" if clamped == value else "clamped"

    async def set_parameters(self, track_id: str, device_ref: str | int, values: dict) -> list[dict]:
        """Several parameters on one device in one engine request.

        Each value is a number, a label ("Legato", "1/8.") or {"label", "value"}.
        Returns one result per parameter (native value, how it was resolved, or
        the error). If the batched request fails, parameters are retried one by
        one so a single bad entry cannot hide the others' outcome.
        """
        dev = await self._resolve_device(track_id, device_ref)
        params = await self.get_parameters(track_id, device_ref)
        outside = self._is_outside_model(dev)
        results, commands, pending = [], [], []
        for name, requested in values.items():
            entry: dict = {"param": name, "requested": requested}
            try:
                key, meta = find_param(params, name)
                native, how = await self._native_value(dev, key, meta, params, requested)
                entry.update(native=native, how=how, ok=True)
                if outside:
                    await self.set_external_parameter(int(dev['id']), meta['name'], native,
                                                      index=self._ext_index(params, key, meta))
                else:
                    cmd = ['set-device-parameter', str(dev['id']), self._param_ref(params, key, meta),
                           repr(float(native))]
                    commands += cmd
                    pending.append((entry, cmd))
            except Exception as e:  # noqa: BLE001 - reported per parameter
                entry.update(ok=False, error=f"{type(e).__name__}: {getattr(e, 'message', e)}"[:300])
            results.append(entry)
        if commands:
            payload: dict = {'op': 'exec', 'commands': commands}
            if self._feature('execSaveFlag'):
                payload['save'] = self.autosave
            try:
                await self._request(payload)
            except ServeCommandFailed:
                for entry, cmd in pending:
                    one: dict = {'op': 'exec', 'commands': cmd}
                    if self._feature('execSaveFlag'):
                        one['save'] = self.autosave
                    try:
                        await self._request(one)
                    except ServeCommandFailed as e:
                        entry.update(ok=False, error=str(e)[:300])
        return results

    async def set_track_volumes(self, volumes: dict) -> None:
        """Several faders in one request (linear gain, clamped to 0..2)."""
        commands: list[str] = []
        for track, volume in volumes.items():
            tid = await self._numeric_track_id(track)
            commands += ["set-track-volume", str(tid), f"{max(0.0, min(2.0, float(volume)))}"]
        if commands:
            await self._exec(*commands)

    @staticmethod
    def _param_ref(params: dict, key, meta: dict) -> str:
        """Name when it is unique on the device, else the index (always the
        index for the engine's wrapper parameters)."""
        if meta.get('owner') == 'host':
            return str(key)
        names = [m.get('name') for m in params.values()]
        return meta['name'] if names.count(meta['name']) == 1 else str(key)

    def _ext_index(self, params: dict, key, meta: dict) -> int | None:
        """Index for set_ext_param. Engines without index support set the
        FIRST parameter carrying the name, so refuse when that would hit a
        different parameter (e.g. the host wrapper's "Wet Level")."""
        first = next(k for k, m in params.items() if m.get('name') == meta['name'])
        if first != key and not self._feature('extParamIndex'):
            from composerbench.errors import UnsupportedOperation
            raise UnsupportedOperation(
                reason=f'this MAGDA build addresses hosted-plugin parameters by name and '
                       f'{meta["name"]!r} is ambiguous on this device',
                hint='rebuild MAGDA with serve protocol 2 (extParamIndex)')
        return int(key)

    async def _param_info(self, dev: dict, key, meta: dict, params: dict) -> dict:
        """Protocol 2: ask the engine for a parameter's labels and value table
        (hosted plugins can report them even when the dump does not)."""
        if not self._feature('paramInfo'):
            return {}
        try:
            reply = await self._request({'op': 'param_info', 'deviceId': int(dev['id']),
                                         'param': self._param_ref(params, key, meta)})
        except ServeCommandFailed:
            return {}
        return {field: reply[field] for field in
                ('text', 'choices', 'choiceValues', 'steps', 'label', 'valueTable', 'default')
                if reply.get(field) not in (None, '', [])}

    async def describe_parameter(self, track_id: str, device_ref: str | int,
                                 parameter: str | int) -> dict:
        """Everything an agent needs to choose a value: range, unit, choices,
        current display text and (protocol 2) the plugin's own value table."""
        dev = await self._resolve_device(track_id, device_ref)
        params = await self.get_parameters(track_id, device_ref)
        key, meta = find_param(params, parameter)
        info = {'index': key, **describe_param(meta)}
        info.update(await self._param_info(dev, key, meta, params))
        if self._is_outside_model(dev):
            info['persistent'] = False
        return info

    # -------------------------------------------------- external plugins ----
    async def list_plugins(self, format_filter: str | None = None) -> list[dict]:
        """Scanned AU/VST3/LV2 plugins: [{id,name,format,isInstrument}]."""
        if self._plugin_cache is None:
            reply = await self._request({"op": "plugins"})
            self._plugin_cache = list(reply.get("plugins", []))
        plugs = [dict(p) for p in self._plugin_cache]
        if format_filter:
            plugs = [p for p in plugs
                     if format_filter.lower() in str(p.get("format", "")).lower()]
        return plugs

    async def add_plugin(self, track_id: str, plugin: str,
                         as_instrument: bool = False) -> int:
        """Host an external AU/VST3 plugin on a track; returns its device id.

        Protocol 2 hosts it inside the project model (listed in `dump`,
        saved with its state). Older engines host it beside the model: it
        still works this session and is listed with ``persistent: False``,
        but it is not saved and a snapshot restore drops it.
        """
        tid = await self._numeric_track_id(track_id)
        reply = await self._request({
            "op": "add_plugin",
            "trackId": tid,
            "plugin": plugin,
            "asInstrument": bool(as_instrument),
        })
        device = int(reply["device"])
        if not reply.get("persistent"):
            desc = next((p for p in await self.list_plugins()
                         if plugin in (p.get("id"), p.get("name"))), {})
            self._ext_devices[device] = {
                "id": device, "trackId": tid, "name": desc.get("name", plugin),
                "pluginId": desc.get("id", plugin),
                "format": desc.get("format", "external"),
                "type": "instrument" if as_instrument else "effect",
                "persistent": False, "parameters": [],
                "note": "hosted outside the project model by this engine: "
                        "not saved, gone after a restart or snapshot restore"}
        return device

    async def remove_plugin(self, device_id: int) -> None:
        await self._request({"op": "remove_device", "deviceId": int(device_id)})
        self._ext_devices.pop(int(device_id), None)

    async def external_parameters(self, device_id: int) -> list[dict]:
        reply = await self._request({"op": "ext_params", "deviceId": int(device_id)})
        return list(reply.get("parameters", []))

    async def set_external_parameter(self, device_id: int, name: str,
                                     value: float, index: int | None = None) -> None:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"normalised value out of range [0,1]: {value}")
        payload = {"op": "set_ext_param", "deviceId": int(device_id),
                   "name": name, "value": float(value)}
        if index is not None and self._feature("extParamIndex"):
            payload["index"] = int(index)
        await self._request(payload)

    async def probe_plugin(self, plugin: str, as_instrument: bool = False,
                           save_to: Path | None = None) -> dict:
        """Instantiate a plugin, dump its parameters, remove it again.

        Returns the manifest dict; optionally writes it as JSON to save_to.
        """
        work = self.project.parent / "_probe"
        work.mkdir(parents=True, exist_ok=True)
        t = await self.create_track("_probe")
        ext_id = await self.add_plugin(t.id, plugin, as_instrument=as_instrument)
        try:
            params = await self.external_parameters(ext_id)
        finally:
            await self.remove_plugin(ext_id)
            await self.delete_track(t.id)
        manifest = {
            "plugin": plugin,
            "probed_by": "composerbench probe-plugin",
            "parameters": params,
        }
        if save_to is not None:
            save_to.parent.mkdir(parents=True, exist_ok=True)
            save_to.write_text(json.dumps(manifest, indent=2))
        return manifest

    # ------------------------------------------------------ semantic roles ----
    async def _locate_device(self, device_id) -> tuple[str, dict]:
        """(track ref, device entry) for a device id from any track."""
        did = int(device_id)
        if did in self._ext_devices:
            dev = self._ext_devices[did]
            return f"track:{dev['trackId']}", dev
        hits = [d for d in (await self._dump_json()).get("devices", [])
                if str(d.get("id")) == str(did)]
        if len(hits) != 1:
            raise EntityNotFound("device", str(device_id))
        return f"track:{hits[0]['trackId']}", hits[0]

    async def shape_param(self, track_id: str, device_ref, spec, owner: str | None = None) -> dict:
        """Apply one recipe ParamSpec (names + label and/or value) to a device.

        Returns ``{"param", "value", "via", ...}`` where ``via`` is "label"
        (resolved against the parameter's own labels), "value" (the authored
        number) or "value-fallback" (the engine could not confirm the label,
        so the authored number was used). ``{"param": None, "error": ...}``
        when nothing matched or the label is unknown and there is no number.
        """
        params = await self.get_parameters(track_id, device_ref)
        hit = match_param(params, spec.names, owner=owner)
        if hit is None:
            return {"param": None, "names": list(spec.names), "error": "no parameter matches"}
        key, meta = hit
        shown = meta.get("stableId") if meta.get("owner") == "host" else meta.get("name")
        via, native = "value", None
        if spec.label is not None:
            candidates = [meta]
            if not choices_of(meta):
                dev = await self._resolve_device(track_id, device_ref)
                info = await self._param_info(dev, key, meta, params)
                if info.get("choices"):
                    candidates.append({**meta, **info})
            for table in reversed(candidates):
                try:
                    native, via = resolve_value(table, spec.label), "label"
                    break
                except UnknownParameterLabel:
                    continue
            if native is None:
                labels = next((choices_of(t) for t in reversed(candidates) if choices_of(t)), [])
                if labels:
                    # The plugin names its states and ours is not one of them:
                    # the authored number would only be a guess.
                    return {"param": shown, "label": spec.label, "choices": labels,
                            "error": "label not offered by this parameter"}
                if spec.value is None:
                    return {"param": shown, "label": spec.label,
                            "error": "engine reports no labels and the recipe has no fallback value"}
                via = "value-fallback"
        if native is None:
            native = resolve_value(meta, spec.value)
        await self.set_parameter(track_id, device_ref, key, native)
        out = {"param": shown, "index": key, "value": native, "via": via}
        if spec.label is not None:
            out["label"] = spec.label
        if getattr(spec, "pinned", False):
            out["pinned"] = True
        return out

    async def shape_params(self, track_id: str, device_ref, specs) -> list[dict]:
        """Apply several ParamSpecs; report each with the resulting display text."""
        results = [await self.shape_param(track_id, device_ref, spec) for spec in specs]
        if any(r.get("param") and "index" in r for r in results):
            try:
                now = await self.get_parameters(track_id, device_ref)
            except Exception:  # noqa: BLE001 - the text is a courtesy
                now = {}
            for r in results:
                meta = now.get(r.get("index"))
                text = label_for(meta) if meta else None
                if text is not None:
                    r["text"] = text
        return results

    @staticmethod
    def _step(result: dict) -> str:
        if not result.get("param"):
            return f"param:?{'/'.join(result.get('names', [])) or result.get('error', '')}"
        if result.get("error"):
            return f"param:{result['param']}=?{result.get('label', '')} ({result['error']})"
        shown = result.get("text") or result.get("label")
        text = f"param:{result['param']}={shown} ({result['value']:.4g})" if shown \
            else f"param:{result['param']}={result['value']:.4g}"
        if result.get("via") == "value-fallback":
            text += f" [label {result.get('label')!r} unconfirmed: engine reports no labels]"
        return text + ("(pinned)" if result.get("pinned") else "")

    async def shape_external_param(self, device_id: int,
                                   names: list[str], value: float) -> str | None:
        """Set the parameter best matching ``names`` (exact before substring,
        the plugin's own before the engine wrapper's). Returns the matched
        name ("host:Wet Level" for the wrapper) or None."""
        from composerbench.semantics.registry import ParamSpec
        try:
            track, dev = await self._locate_device(device_id)
        except EntityNotFound:
            return None
        result = await self.shape_param(track, dev["id"], ParamSpec(names=list(names), value=value))
        return result.get("param") if not result.get("error") else None

    async def shape_all_params(self, device_id: int,
                               names: list[str], value: float) -> list[str]:
        """Set EVERY parameter of the plugin whose name contains any of
        ``names``. Use when a plugin has per-scene duplicates (Surge A/B)."""
        try:
            track, dev = await self._locate_device(device_id)
            params = await self.get_parameters(track, dev["id"])
        except Exception:  # noqa: BLE001
            return []
        lowered = [n.lower() for n in names]
        own = {k: m for k, m in params.items() if m.get("owner") != "host"}
        pool = own if any(any(n in m["name"].lower() for n in lowered) for m in own.values()) else params
        hits = [(k, m) for k, m in pool.items() if any(n in m["name"].lower() for n in lowered)]
        for key, _ in hits:
            await self.set_parameter(track, dev["id"], key, value)
        return [m.get("stableId") if m.get("owner") == "host" else m["name"] for _, m in hits]

    async def apply_role(self, track_name: str, recipe, registry=None) -> dict:
        """Apply a semantic RoleRecipe to a (new or existing) track.

        Creates the track if absent, loads the recipe's engine via alias,
        shapes params (labels such as "LP 12 dB" are resolved against the
        plugin's own), attaches space FX and sets the level. Returns a
        report with one step per action plus the per-parameter results.
        """
        from composerbench.semantics.registry import ParamSpec, load_default

        reg = registry or load_default()
        plugin_id = reg.resolve_engine(recipe.engine)
        steps: list[str] = []

        existing = {t.name: t for t in await self.get_tracks()}
        if track_name in existing:
            tid = existing[track_name].id
            steps.append(f"track:{track_name}=existing")
        else:
            t = await self.create_track(track_name)
            tid = t.id
            steps.append(f"track:{track_name}=created")

        is_internal = recipe.engine.startswith("magda_") or recipe.engine == "4osc"
        if is_internal:
            await self.load_device(tid, plugin_id)
            entries = [d for d in await self._device_entries(tid) if d.get("name") == plugin_id]
            device = entries[-1]["id"] if entries else None
            steps.append(f"engine:{recipe.engine}=internal")
        else:
            device = await self.add_plugin(tid, plugin_id, as_instrument=recipe.as_instrument)
            steps.append(f"engine:{recipe.engine}=device{device}")

        results: list[dict] = []
        if recipe.params:
            if device is not None:
                results = await self.shape_params(tid, device, recipe.params)
            else:   # engine entry not identifiable: try every device on the track
                for spec in recipe.params:
                    result = {"param": None, "names": list(spec.names), "error": "no device"}
                    for dev in await self._device_entries(tid):
                        result = await self.shape_param(tid, dev["id"], spec)
                        if result.get("param") and not result.get("error"):
                            break
                    results.append(result)
            steps.extend(self._step(r) for r in results)

        if recipe.space.reverb:
            rid = await self.add_fx(tid, reg.resolve_engine(recipe.space.reverb))
            wet = recipe.space.reverb_wet
            if wet is not None:
                # reverb_wet has always driven the engine's wrapper Wet Level
                # (a gain on the whole reverb output); plugins without that
                # wrapper fall back to their own wet/mix control.
                result = await self.shape_param(tid, rid, ParamSpec(names=["Wet Level"], value=wet), owner="host")
                if not result.get("param"):
                    result = await self.shape_param(
                        tid, rid, ParamSpec(names=["wet level", "wet", "mix"], value=wet))
                steps.append(f"reverb:{recipe.space.reverb}/{result.get('param') or '?'}={wet}")
            else:
                steps.append(f"reverb:{recipe.space.reverb}")
        if recipe.space.delay:
            did = await self.add_fx(tid, reg.resolve_engine(recipe.space.delay))
            steps.append(f"delay:{recipe.space.delay}=device{did}")

        await self.set_track_volume(tid, recipe.level)
        steps.append(f"level={recipe.level}")
        return {"role": recipe.slug, "track": track_name, "device": device,
                "steps": steps, "params": results}
