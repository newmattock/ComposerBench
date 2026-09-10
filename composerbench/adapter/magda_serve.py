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

MagdaServeAdapter speaks this protocol. It subclasses MagdaCliAdapter and
inherits snapshots/aliases/guards; only transport-sensitive methods are
overridden. The win: one engine boot per session instead of one per command,
which turns long composition pipelines from minutes of boot overhead into
milliseconds of dispatch.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from composerbench.adapter.base import RenderRequest
from composerbench.adapter.magda import MagdaCliAdapter
from composerbench.errors import (
    ConnectionLost,
    EntityNotFound,
    ComposerBenchError,
    RenderFailed,
)


class ServeCommandFailed(ComposerBenchError):
    code = "SERVE_COMMAND_FAILED"


class AddFxFailed(ComposerBenchError):
    code = "ADD_FX_FAILED"


def with_suppressed_kill(proc: asyncio.subprocess.Process) -> None:
    """Kill a booting daemon, tolerating an already-exited process."""
    try:
        proc.kill()
    except (ProcessLookupError, Exception):  # noqa: BLE001 - best effort
        pass


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

    def capabilities(self):
        from dataclasses import replace
        return replace(super().capabilities(), plugin_parameters=True, effects=True,
                       semantic_roles=True)

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
                    raise asyncio.TimeoutError
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
        except asyncio.TimeoutError as e:
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
            except Exception:  # noqa: BLE001 - best-effort shutdown
                pass
        await self._drop_session()

    async def _drop_session(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
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
        except asyncio.TimeoutError as e:
            await self._drop_session()
            raise ConnectionLost(reason="serve-request-timeout") from e

    async def _request(self, payload: dict) -> dict:
        """Envelope-checked request: raises on {ok:false}."""
        reply = await self._send_raw(payload)
        if isinstance(reply, dict) and not reply.get("ok", False):
            raise ServeCommandFailed(error=str(reply.get("error", "?")))
        return reply

    # ------------------------------------------------- overridden transports --
    async def _exec(self, *commands: str) -> str:
        """Batched edit via the persistent engine (raises on any failure)."""
        await self._request({"op": "exec", "commands": list(commands)})
        return ""

    async def _dump_json(self) -> dict:
        """The dump response line IS the project document."""
        reply = await self._send_raw({"op": "dump"})
        if not isinstance(reply, dict) or "tracks" not in reply:
            raise ConnectionLost(reason="serve-dump-malformed",
                                 got=str(reply)[:120])
        return reply

    async def render(self, request: RenderRequest) -> Path:
        if request.track_ids is not None:
            from composerbench.errors import UnsupportedOperation
            raise UnsupportedOperation(reason='isolated-track-render-not-supported; refusing to return a full mix as a stem')
        out = Path(request.output_path)
        payload: dict = {"op": "render", "wav": str(out),
                         "bitDepth": request.bit_depth}
        if request.start_beats is not None:
            payload["from"] = f"{request.start_beats}beats"
        if request.length_beats is not None:
            payload["to"] = f"{(request.start_beats or 0) + request.length_beats}beats"
        await self._request(payload)
        if not out.exists():
            raise RenderFailed(output=str(out))
        return out

    async def save_project(self) -> None:
        await self._request({"op": "save"})

    # --------------------------------------------- new capabilities (FX/params)--
    async def _device_entries(self, track_id: str) -> list[dict]:
        tid = await self._numeric_track_id(track_id)
        data = await self._dump_json()
        return [d for d in data.get("devices", []) if d.get("trackId") == tid]

    async def add_fx(self, track_id: str, plugin_id: str,
                     as_instrument: bool = False) -> int:
        """Attach an effect to the track's chain; returns its device id.

        Accepts internal effect ids (magda_reverb...) OR external plugin
        ids/names (e.g. "VST3-Dragonfly Room Reverb-..."). Externals route
        through add_plugin because the internal add-fx path only knows the
        compiled registry. Returns the numeric/ext device handle usable with
        set_parameter / set_external_parameter.
        """
        if not str(plugin_id).startswith("magda_"):
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

    async def _resolve_device(self, track_id, device_ref):
        entries = await self._device_entries(track_id)
        want = str(device_ref).removeprefix('device:')
        matches = [d for d in entries if str(d['id']) == want or d.get('name') == want]
        if len(matches) != 1:
            raise EntityNotFound('unambiguous device', str(device_ref), [str(d['id']) for d in entries])
        return matches[0]

    async def get_parameters(self, track_id: str, device_ref: str | int) -> dict:
        """Live parameter values and bounds, including normalized external plugins."""
        dev = await self._resolve_device(track_id, device_ref)
        if dev.get('format', 'internal').lower() != 'internal':
            return {i: {'name': p['name'], 'value': p['value'], 'min': 0.0,
                        'max': 1.0, 'unit': 'normalized', 'stableId': p['name']}
                    for i, p in enumerate(await self.external_parameters(int(dev['id'])))}
        return {p['index']: {'name': p.get('name', ''), 'stableId': p.get('stableId', ''),
                            'value': p.get('value', 0.0), 'min': p.get('min', 0.0),
                            'max': p.get('max', 1.0), 'unit': p.get('unit', '')}
                for p in dev.get('parameters', [])}

    async def set_parameter(self, track_id: str, device_ref: str | int,
                            parameter: str | int, value: float) -> None:
        dev = await self._resolve_device(track_id, device_ref)
        if dev.get('format', 'internal').lower() != 'internal':
            params = await self.get_parameters(track_id, device_ref)
            hits = [p for i, p in params.items() if str(i) == str(parameter)
                    or p['name'] == str(parameter)]
            if len(hits) != 1 or sum(p['name'] == hits[0]['name'] for p in params.values()) != 1:
                raise ValueError('external parameter name must be unambiguous')
            await self.set_external_parameter(int(dev['id']), hits[0]['name'], value)
            return
        await self._request({'op': 'exec', 'commands': [
            'set-device-parameter', str(dev['id']), str(parameter), repr(float(value))]})

    # -------------------------------------------------- external plugins ----
    async def list_plugins(self, format_filter: str | None = None) -> list[dict]:
        """Scanned AU/VST3/LV2 plugins: [{id,name,format,isInstrument}]."""
        reply = await self._request({"op": "plugins"})
        plugs = list(reply.get("plugins", []))
        if format_filter:
            plugs = [p for p in plugs
                     if format_filter.lower() in str(p.get("format", "")).lower()]
        return plugs

    async def add_plugin(self, track_id: str, plugin: str,
                         as_instrument: bool = False) -> int:
        """Host an external AU/VST3 plugin on a track; returns its ext id."""
        reply = await self._request({
            "op": "add_plugin",
            "trackId": await self._numeric_track_id(track_id),
            "plugin": plugin,
            "asInstrument": bool(as_instrument),
        })
        return int(reply["device"])

    async def remove_plugin(self, device_id: int) -> None:
        await self._request({"op": "remove_device", "deviceId": int(device_id)})

    async def external_parameters(self, device_id: int) -> list[dict]:
        reply = await self._request({"op": "ext_params", "deviceId": int(device_id)})
        return list(reply.get("parameters", []))

    async def set_external_parameter(self, device_id: int, name: str,
                                     value: float) -> None:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"normalised value out of range [0,1]: {value}")
        await self._request({
            "op": "set_ext_param",
            "deviceId": int(device_id),
            "name": name,
            "value": float(value),
        })

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
    async def shape_external_param(self, device_id: int,
                                   names: list[str], value: float) -> str | None:
        """Set the first external parameter matching any of `names`
        (case-insensitive substring). Returns the matched name or None."""
        try:
            params = await self.external_parameters(int(device_id))
        except Exception:
            return None
        lowered = [n.lower() for n in names]
        hit = next((p for p in params
                    if any(n in p["name"].lower() for n in lowered)), None)
        if hit is None:
            return None
        await self.set_external_parameter(device_id, hit["name"], value)
        return hit["name"]

    async def shape_all_params(self, device_id: int,
                               names: list[str], value: float) -> list[str]:
        """Set EVERY external parameter matching any of `names`. Returns the
        matched names. Use when a plugin has per-scene duplicates (Surge A/B)."""
        try:
            params = await self.external_parameters(int(device_id))
        except Exception:
            return []
        lowered = [n.lower() for n in names]
        hits = [p["name"] for p in params
                if any(n in p["name"].lower() for n in lowered)]
        for name in hits:
            await self.set_external_parameter(device_id, name, value)
        return hits

    async def apply_role(self, track_name: str, recipe, registry=None) -> dict:
        """Apply a semantic RoleRecipe to a (new or existing) track.

        Creates the track if absent, loads the recipe's engine via alias,
        shapes params by candidate names, attaches space FX, sets level.
        Returns an applied-steps report dict.
        """
        from composerbench.semantics.registry import load_default

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
        ext_device = None
        if is_internal:
            await self.load_device(tid, plugin_id)
            steps.append(f"engine:{recipe.engine}=internal")
        else:
            ext_device = await self.add_plugin(
                tid, plugin_id, as_instrument=recipe.as_instrument)
            steps.append(f"engine:{recipe.engine}=device{ext_device}")

        for spec in recipe.params:
            if ext_device is not None:
                matched = await self.shape_external_param(
                    ext_device, spec.names, spec.value)
            else:
                matched = None
                for dev in await self.get_devices_full(tid):
                    try:
                        await self.set_parameter(tid, dev["id"], spec.names[0],
                                                 spec.value)
                        matched = spec.names[0]
                        break
                    except Exception:
                        continue
            steps.append(f"param:{matched or '?'}={spec.value}"
                         + ("(pinned)" if spec.pinned else ""))

        if recipe.space.reverb:
            rid = await self.add_fx(tid, reg.resolve_engine(recipe.space.reverb))
            wet = recipe.space.reverb_wet
            if wet is not None:
                matched = await self.shape_external_param(
                    rid, ["wet level", "wet", "mix"], wet)
                steps.append(f"reverb:{recipe.space.reverb}/{matched or '?'}={wet}")
            else:
                steps.append(f"reverb:{recipe.space.reverb}")
        if recipe.space.delay:
            did = await self.add_fx(tid, reg.resolve_engine(recipe.space.delay))
            steps.append(f"delay:{recipe.space.delay}=device{did}")

        await self.set_track_volume(tid, recipe.level)
        steps.append(f"level={recipe.level}")
        return {"role": recipe.slug, "track": track_name, "steps": steps}
