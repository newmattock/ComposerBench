"""ComposerBench MCP server (spec §22, Milestone 3).

Exposes ComposerBench *semantic* operations to external agents over stdio,
newline-delimited JSON-RPC 2.0 (MCP 2025-11-25 / 2026-07-28 lifecycle).

    COMPOSERBENCH_ADAPTER=magda COMPOSERBENCH_CLI=... COMPOSERBENCH_PROJECT=... \
        python -m composerbench.mcp.server

Tool-originated failures are reported with isError=true inside the result
(spec rule), so agents can see and self-correct.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from composerbench.client import ComposerBench
from composerbench.composition.state import CompositionPlan, Intent
from composerbench.errors import ComposerBenchError
from composerbench.model import Note

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "composerbench", "version": "0.0.1"}

_OBJ = {"type": "object", "properties": {}}


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


def _note_param(desc: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": desc,
        "properties": {
            "start": {"type": "number"},
            "duration": {"type": "number", "default": 1.0},
            "pitch": {"type": ["integer", "string"], "description": "MIDI number or 'C4'"},
            "velocity": {"type": "integer", "minimum": 1, "maximum": 127},
        },
        "required": ["start", "pitch"],
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "music_project_init",
        "description": "Create a new empty workstation project at the configured path.",
        "inputSchema": _OBJ,
    },
    {
        "name": "music_project_inspect",
        "description": "Hierarchical project overview: title, tempo, length, tracks. "
                       "Start here; drill down with music_track_inspect.",
        "inputSchema": _OBJ,
    },
    {
        "name": "music_track_inspect",
        "description": "Track detail: clips, note statistics, devices.",
        "inputSchema": _schema({"track": {"type": "string"}}, ["track"]),
    },
    {
        "name": "music_track_create",
        "description": "Create a new track.",
        "inputSchema": _schema({"name": {"type": "string"}}, ["name"]),
    },
    {
        "name": "music_clip_create",
        "description": "Create a MIDI clip on a track. start/length in beats.",
        "inputSchema": _schema({
            "track": {"type": "string"},
            "name": {"type": "string"},
            "start_beats": {"type": "number"},
            "length_beats": {"type": "number"},
        }, ["track", "name", "start_beats", "length_beats"]),
    },
    {
        "name": "music_notes_add",
        "description": "Add notes to a clip on a track.",
        "inputSchema": _schema({
            "track": {"type": "string"},
            "clip": {"type": "string"},
            "notes": {"type": "array", "items": _note_param("one note")},
        }, ["track", "clip", "notes"]),
    },
    {
        "name": "music_device_load",
        "description": "Load a built-in instrument onto a track (e.g. '4osc'). "
                       "Required for MIDI clips to make sound.",
        "inputSchema": _schema({
            "track": {"type": "string"},
            "device": {"type": "string"},
        }, ["track", "device"]),
    },
    {
        "name": "music_render",
        "description": "Offline-render audio. Omit range for full song.",
        "inputSchema": _schema({
            "output_path": {"type": "string"},
            "start_beats": {"type": "number"},
            "length_beats": {"type": "number"},
            "bit_depth": {"type": "integer", "enum": [16, 24], "default": 24},
        }),
    },
    {
        "name": "music_intent_read",
        "description": "Read artistic intent (.musicbench/intent.yaml).",
        "inputSchema": _OBJ,
    },
    {
        "name": "music_intent_update",
        "description": "Write full artistic intent object.",
        "inputSchema": _schema({"intent": {"type": "object"}}, ["intent"]),
    },
    {
        "name": "music_plan_read",
        "description": "Read composition plan (sections/bars/target energies).",
        "inputSchema": _OBJ,
    },
    {
        "name": "music_plan_update",
        "description": "Write full composition plan object.",
        "inputSchema": _schema({"plan": {"type": "object"}}, ["plan"]),
    },
    {
        "name": "music_snapshot_create",
        "description": "Snapshot project state before risky edits.",
        "inputSchema": _schema({"label": {"type": "string"}}, ["label"]),
    },
    {
        "name": "music_snapshot_restore",
        "description": "Restore a snapshot by id returned from create.",
        "inputSchema": _schema({"snapshot_id": {"type": "string"}}, ["snapshot_id"]),
    },
]


# One schema registry serves discovery and input validation.
from composerbench.agent.actions import Action
from composerbench.evaluation.comparison import Objective
from composerbench.production import Slice, GainPoint
_STRING = {'type': 'string', 'minLength': 1}
_NUMBER = {'type': 'number'}
_ACTION = Action.model_json_schema()
_ACTION_DEFS = _ACTION.pop('$defs', {})
_BATCH = {'actions': {'type': 'array', 'items': _ACTION},
          'timeout_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 3600}}
_NEW_TOOLS = [
    ('music_audio_visual_review', 'Local fixed-scale multiresolution spectrograms, peak/RMS envelopes, stereo-band cancellation and aligned-stem overlap evidence. Writes PNG/HTML/JSON; no audio model or quality verdict. Requires visual extra.',
     {'path': _STRING, 'output_dir': _STRING, 'start_seconds': {'type': 'number', 'minimum': 0},
      'duration_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 180, 'default': 12},
      'stems': {'type': 'object', 'maxProperties': 8, 'additionalProperties': _STRING}}, ['path']),
    ('music_audio_inspect', 'Measure a mono/stereo WAV excerpt: LUFS/true peak, frequency power, mono cancellation and timed findings. No quality score.',
     {'path': _STRING, 'start_seconds': {'type': 'number', 'minimum': 0},
      'duration_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 180}}, ['path']),
    ('music_audio_compare', 'Create verified constant-gain loudness-matched A/B and mono WAVs. Review rejected revision renders or compare a reference; never automatically declares a winner.',
     {'baseline': _STRING, 'candidate': _STRING, 'output_dir': _STRING,
      'start_seconds': {'type': 'number', 'minimum': 0},
      'candidate_start_seconds': {'type': 'number', 'minimum': 0},
      'duration_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 180},
      'ceiling_dbtp': {'type': 'number', 'minimum': -24, 'maximum': -0.5},
      'target_lufs': {'type': 'number', 'minimum': -36, 'maximum': -9, 'default': -18}}, ['baseline', 'candidate']),
    ('music_audio_resample', 'Create a new float WAV from slices: reverse, repeat, band-limited varispeed and output-time gain envelope. Not independent pitch/time/formant processing; no DAW insertion.',
     {'source': _STRING, 'output_dir': _STRING,
      'slices': {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': Slice.model_json_schema()},
      'gain_points': {'type': 'array', 'maxItems': 4096, 'items': GainPoint.model_json_schema()},
      'fade_ms': {'type': 'number', 'minimum': 0, 'maximum': 100}}, ['source', 'slices']),
    ('music_capabilities', 'Discover supported operations before editing.', {}, []),
    ('music_devices_inspect', 'Inspect live device IDs and metadata.', {'track': _STRING}, ['track']),
    ('music_parameters_inspect', 'Inspect parameter IDs, values, units and ranges.', {'track': _STRING, 'device': _STRING}, ['track', 'device']),
    ('music_parameter_set', 'Set a validated parameter in a recoverable transaction.', {'track': _STRING, 'device': _STRING, 'parameter': _STRING, 'value': _NUMBER}, ['track', 'device', 'parameter', 'value']),
    ('music_volume_set', 'Set track gain (linear 0..2).', {'track': _STRING, 'volume': {'type': 'number', 'minimum': 0, 'maximum': 2}}, ['track', 'volume']),
    ('music_fx_add', 'Attach an effect and return its device ID.', {'track': _STRING, 'name': _STRING}, ['track', 'name']),
    ('music_roles_list', 'List available semantic sound recipes.', {}, []),
    ('music_role_apply', 'Apply a sound recipe transactionally.', {'track': _STRING, 'role': _STRING}, ['track', 'role']),
    ('music_batch_edit', 'Preflight and apply an all-or-nothing edit batch. No artistic judgment.', _BATCH, ['actions']),
    ('music_critique', 'Analyze a rendered WAV against the current plan and intent.', {'path': _STRING}, ['path']),
    ('music_revision_run', 'Render baseline and candidate, evaluate goal metrics, keep or restore; persist evidence.',
     {**_BATCH, 'goal': _STRING, 'objectives': {'type': 'array', 'items': Objective.model_json_schema()},
      'critic': {'type': 'boolean'}, 'auto_fix': {'type': 'boolean'},
      'audition_start_seconds': {'type': 'number', 'minimum': 0},
      'audition_duration_seconds': {'type': 'number', 'minimum': 0.4, 'maximum': 30},
      'max_peak_dbfs': {'type': ['number', 'null']}}, ['goal', 'actions']),
    ('music_revision_history', 'Read persisted decisions and before/after evidence.', {}, []),
]
for name, description, props, required in _NEW_TOOLS:
    schema = _schema(props, required)
    schema['additionalProperties'] = False
    if 'actions' in props: schema['$defs'] = _ACTION_DEFS
    TOOLS.append({'name': name, 'description': description, 'inputSchema': schema})
for tool in TOOLS:
    tool['outputSchema'] = {'type': 'object'}


@dataclass
class ServerConfig:
    adapter: str = "fake"          # fake | magda
    cli_path: str = ""
    project_path: str = ""
    serve: bool = False


def build_client(cfg: ServerConfig) -> ComposerBench:
    if cfg.adapter in ("magda", "magda_serve"):
        return ComposerBench.connect_magda(cfg.cli_path, cfg.project_path, serve=cfg.serve or cfg.adapter == "magda_serve")
    if cfg.adapter != "fake":
        raise ValueError(f"unknown adapter {cfg.adapter}")
    return ComposerBench.connect_fake(cfg.project_path or ".")


class MCPServer:
    def __init__(self, client: ComposerBench):
        import asyncio
        self.client = client
        self._call_lock = asyncio.Lock()

    # ---- JSON-RPC plumbing ------------------------------------------------------
    def dispatch(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        req_id = request.get("id")
        if method == "initialize":
            return self._result(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return self._result(req_id, {"tools": TOOLS})
        if method == "tools/call":
            params = request.get("params") or {}
            return self._result(req_id, self._call_tool(
                params.get("name", ""), params.get("arguments") or {}))
        if req_id is not None:
            return self._error(req_id, -32601, f"method not found: {method}")
        return None

    @staticmethod
    def _result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_text(payload: Any, is_error: bool = False) -> dict[str, Any]:
        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, default=str)
        structured = payload if isinstance(payload, dict) else {"value": payload}
        return {"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": is_error}

    # ---- tool implementations -----------------------------------------------------
    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Async-aware tool invocation for direct embedding."""
        try:
            import jsonschema
            spec = next((t for t in TOOLS if t["name"] == name), None)
            if spec is None: raise KeyError(f"unknown tool: {name}")
            jsonschema.validate(args, spec["inputSchema"])
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                raise KeyError(f"unknown tool: {name}")
            async with self._call_lock:
                out = handler(args)
                if hasattr(out, "__await__"):
                    out = await out
            return out
        except ComposerBenchError as e:
            return self._tool_text(e.to_dict(), is_error=True)
        except Exception as e:  # noqa: BLE001
            return self._tool_text({"error": type(e).__name__, "message": getattr(e, "message", str(e))[:1500]}, is_error=True)

    def _call_tool(self, name, args):
        import asyncio
        return asyncio.run(self.call_tool(name, args))

    async def adispatch(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        req_id = request.get("id")
        if method == "tools/call":
            params = request.get("params") or {}
            return self._result(req_id, await self.call_tool(
                params.get("name", ""), params.get("arguments") or {}))
        return self.dispatch(request)

    # ---- tools -------------------------------------------------------------------
    async def _tool_music_project_init(self, _: dict) -> dict:
        info = await self.client.initialize()
        return self._tool_text({"created": True, "project": info.model_dump()})

    async def _tool_music_project_inspect(self, _: dict) -> dict:
        info = await self.client.project()
        tracks = await self.client.tracks()
        return self._tool_text({
            "title": info.title,
            "tempo_bpm": info.tempo_bpm,
            "length_beats": info.length_beats,
            "tracks": [{"id": t.id, "stable_id": t.stable_id, "name": t.name, "clips": len(await self.client.clips(t.id))}
                       for t in tracks],
        })

    async def _tool_music_track_inspect(self, args: dict) -> dict:
        track = await self.client.track(args["track"])
        clips = await self.client.clips(track.name)
        notes = await self.client.notes(track.name)
        from composerbench.analysis.symbolic import analyze_notes
        stats = analyze_notes(notes).to_dict()
        return self._tool_text({
            "track": track.name,
            "track_id": track.id,
            "stable_id": track.stable_id,
            "devices": [d.model_dump() for d in await self.client.devices(track.id)],
            "volume": track.volume,
            "muted": track.muted,
            "clips": [{"id": c.id, "stable_id": c.stable_id, "name": c.name, "start": c.start, "length": c.length}
                      for c in clips],
            "symbolic_stats": stats,
        })

    async def _tool_music_track_create(self, args: dict) -> dict:
        track = await self.client.create_track(args["name"])
        return self._tool_text({"created": track.name, "track_id": track.id, "stable_id": track.stable_id})

    async def _tool_music_clip_create(self, args: dict) -> dict:
        clip = await self.client.create_clip(
            args["track"], args["name"],
            float(args["start_beats"]), float(args["length_beats"]))
        return self._tool_text({"clip_id": clip.id, "stable_id": clip.stable_id, "name": clip.name})

    async def _tool_music_notes_add(self, args: dict) -> dict:
        from composerbench.adapter.magda import parse_note_pitch
        notes = [
            Note(
                start=float(n["start"]),
                duration=float(n.get("duration", 1.0)),
                pitch=parse_note_pitch(n["pitch"]),
                velocity=int(n.get("velocity", 100)),
            )
            for n in args["notes"]
        ]
        added = await self.client.add_notes(args["track"], clip=args.get("clip"), notes=notes)
        return self._tool_text({"added": added})

    async def _tool_music_device_load(self, args: dict) -> dict:
        result = await self.client.load_device(args['track'], args['device'])
        device = result['results'][0]
        return self._tool_text({'loaded': device['name'], 'device_id': device['id'], 'stable_id': device.get('stable_id')})

    async def _tool_music_render(self, args: dict) -> dict:
        from composerbench.adapter.base import RenderRequest
        out = Path(args.get("output_path", "render.wav"))
        req = RenderRequest(
            output_path=out,
            start_beats=args.get("start_beats"),
            length_beats=args.get("length_beats"),
            bit_depth=args.get("bit_depth", 24),
        )
        path = await self.client.render(req)
        from composerbench.analysis.audio import analyze_wav
        stats = analyze_wav(path).to_dict()
        return self._tool_text({"file": str(path), "audio": stats})

    async def _tool_music_intent_read(self, _: dict) -> dict:
        intent = await self.client.read_intent()
        return self._tool_text(intent.to_dict())

    async def _tool_music_intent_update(self, args: dict) -> dict:
        intent = Intent.from_dict(args["intent"])
        path = await self.client.write_intent(intent)
        return self._tool_text({"written": str(path)})

    async def _tool_music_plan_read(self, _: dict) -> dict:
        plan = await self.client.read_plan()
        return self._tool_text(plan.to_dict())

    async def _tool_music_plan_update(self, args: dict) -> dict:
        plan = CompositionPlan.from_dict(args["plan"])
        path = await self.client.write_plan(plan)
        return self._tool_text({"written": str(path)})

    async def _tool_music_snapshot_create(self, args: dict) -> dict:
        snap = await self.client.snapshot(args["label"])
        return self._tool_text({"snapshot_id": snap})

    async def _tool_music_snapshot_restore(self, args: dict) -> dict:
        await self.client.restore_snapshot(args["snapshot_id"])
        return self._tool_text({"restored": args["snapshot_id"]})


    async def _tool_music_capabilities(self, args):
        return self._tool_text(self.client.capabilities())

    async def _tool_music_devices_inspect(self, args):
        return self._tool_text({'devices': [d.model_dump() for d in await self.client.devices(args['track'])]})

    async def _tool_music_parameters_inspect(self, args):
        return self._tool_text({'parameters': await self.client.parameters(args['track'], args['device'])})

    async def _tool_music_parameter_set(self, args):
        return self._tool_text(await self.client.set_parameter(**args))

    async def _tool_music_volume_set(self, args):
        return self._tool_text(await self.client.set_volume(**args))

    async def _tool_music_fx_add(self, args):
        return self._tool_text(await self.client.add_fx(**args))

    async def _tool_music_roles_list(self, args):
        return self._tool_text({'roles': self.client.roles()})

    async def _tool_music_role_apply(self, args):
        return self._tool_text(await self.client.apply_role(**args))

    async def _tool_music_batch_edit(self, args):
        return self._tool_text(await self.client.batch(**args))

    async def _tool_music_critique(self, args):
        return self._tool_text(await self.client.critique(**args))

    async def _tool_music_audio_visual_review(self, args):
        return self._tool_text(await self.client.audio_visual_review(**args))

    async def _tool_music_audio_inspect(self, args):
        return self._tool_text(await self.client.audio_inspect(**args))

    async def _tool_music_audio_compare(self, args):
        return self._tool_text(await self.client.audio_compare(**args))

    async def _tool_music_audio_resample(self, args):
        return self._tool_text(await self.client.audio_resample(**args))

    async def _tool_music_revision_run(self, args):
        return self._tool_text(await self.client.revise(**args))

    async def _tool_music_revision_history(self, args):
        return self._tool_text({'revisions': self.client.revision_history()})


def serve_stdio(cfg: ServerConfig | None = None) -> int:
    import asyncio
    cfg = cfg or ServerConfig(
        adapter=os.environ.get('COMPOSERBENCH_ADAPTER', 'fake'),
        cli_path=os.environ.get('COMPOSERBENCH_CLI', ''),
        project_path=os.environ.get('COMPOSERBENCH_PROJECT', ''),
        serve=os.environ.get('COMPOSERBENCH_SERVE', '').lower() in ('1', 'true'),
    )
    server = MCPServer(build_client(cfg))

    async def main():
        try:
            while True:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line: break
                if not line.strip(): continue
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict): raise ValueError('request must be an object')
                except (json.JSONDecodeError, ValueError) as e:
                    response = {'jsonrpc': '2.0', 'id': None,
                                'error': {'code': -32700, 'message': str(e)}}
                else:
                    response = await server.adispatch(request)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + '\n')
                    sys.stdout.flush()
        finally:
            await server.client.close()
    asyncio.run(main())
    return 0


if __name__ == '__main__':
    raise SystemExit(serve_stdio())
