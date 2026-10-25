"""ComposerBench MCP server (spec §22, Milestone 3).

Exposes ComposerBench *semantic* operations to external agents over stdio,
newline-delimited JSON-RPC 2.0 (MCP 2025-11-25 / 2026-07-28 lifecycle).

    python -m composerbench.mcp.server                 # real MAGDA if it can be found
    composerbench mcp install --client all             # register with Claude/Cursor

Configuration (environment or flags):
    COMPOSERBENCH_ADAPTER    auto (default) | magda_serve | magda | fake
    COMPOSERBENCH_CLI        magda_cli binary (default: discovered under ~/magda-core)
    COMPOSERBENCH_PROJECT    optional project to open at start
    COMPOSERBENCH_WORKSPACE  where music_project_new creates projects
                          (default ~/Music/ComposerBench/projects)

"auto" uses the persistent MAGDA engine whenever the binary exists and only
falls back to the fake emulator (which cannot make music) when it does not;
music_status says which one is active.

Tool-originated failures are reported with isError=true inside the result
(spec rule), so agents can see and self-correct.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from composerbench.agent.actions import Action
from composerbench.client import ComposerBench
from composerbench.composition.state import CompositionPlan, Intent
from composerbench.engine import CLI_CANDIDATES, discover_cli
from composerbench.errors import (
    ComposerBenchError,
    MagdaCliNotFound,
    NoProject,
    ProjectExists,
    ProjectNotFound,
    UnsupportedOperation,
)
from composerbench.evaluation.comparison import Objective
from composerbench.model import Note
from composerbench.production import GainPoint, Slice

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "composerbench", "version": "0.1.0"}

DEFAULT_WORKSPACE = Path.home() / "Music" / "ComposerBench" / "projects"

INSTRUCTIONS = """ComposerBench drives the MAGDA DAW (a real engine with synths, drum machines, \
effects and hosted VST3/AU plugins) and renders audio you can analyze.
1. music_status, then music_project_new (or music_project_open).
2. music_track_create -> music_device_load (built-ins: magda_polysynth, magda_fm, 4osc, \
magda_kick/snare/clap/hat/tom, magda_bell, magda_marimba) or music_plugins_list + \
music_plugin_add (e.g. Surge XT) -> music_fx_add (magda_reverb, magda_delay, magda_eq, \
magda_compressor, magda_saturator, magda_bitcrusher, magda_filter, magda_chorus, magda_utility...).
3. Make ONE clip per track that spans the song (clips on the same track play one after \
another; they never stack). Write each whole part with music_notes_set (one call per clip; \
note starts are absolute beats).
4. music_parameters_inspect / music_parameter_describe show ranges, units, choices and the \
current meaning; music_parameter_set accepts numbers or labels such as "Hall", "1/8.", "Legato".
5. music_render (keep peaks below 0 dBFS by lowering track volumes), then music_audio_inspect \
/ music_audio_visual_review. Batches and revisions are transactional; music_project_save persists.

Whole songs (fast path, see docs/AGENTIC_PRODUCTION.md):
a. Write a score (notes per part, sections, chords; composerbench.composition helpers) and check it \
with music_score_check (semitone clashes, ranges).
b. Write a song spec: tracks = preset (music_presets) and/or instrument + FX with parameters by \
name and label (music_device_catalog). music_song_check preflights it in milliseconds.
c. music_song_build renders the mix and one WAV per stem as a background job; poll \
music_job_status. Read session.json problems / readback_mismatch.
d. music_mixdown turns the stems into a master: automation, score-keyed sidechain, cuts before \
drops, tape stop/stutter/reverse swell, bus glue, LUFS target + true-peak ceiling. Iterate on the \
mix spec (seconds per pass) using music_section_report numbers instead of guessing."""

_OBJ = {"type": "object", "properties": {}}

log = logging.getLogger(__name__)


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
        "description": "Create the project configured by COMPOSERBENCH_PROJECT. Prefer music_project_new.",
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
    ('music_parameter_set', 'Set a validated parameter in a recoverable transaction. value is a number in the parameter\'s range or a choice label ("Hall", "1/8.", "Legato").', {'track': _STRING, 'device': _STRING, 'parameter': _STRING, 'value': {'type': ['number', 'string']}}, ['track', 'device', 'parameter', 'value']),
    ('music_volume_set', 'Set track gain (linear 0..2).', {'track': _STRING, 'volume': {'type': 'number', 'minimum': 0, 'maximum': 2}}, ['track', 'volume']),
    ('music_fx_add', 'Attach an effect and return its device ID. track may be "master" (master bus insert). position "post" puts it after the fader; sidechain_from keys a pre-fader device (the "sidechain" ducker, a compressor) from another track. Master, position and sidechain need the protocol-3 engine.',
     {'track': _STRING, 'name': _STRING, 'position': {'type': 'string', 'enum': ['pre', 'post']},
      'sidechain_from': _STRING, 'sidechain_type': {'type': 'string', 'enum': ['midi', 'audio']}}, ['track', 'name']),
    ('music_mixer_set', 'Channel settings in the engine: volume in dB, pan -1..1, mute, solo, and post-fader sends to aux/group tracks ([{to, level_db} | {to, remove: true}]). track may be "master". Returns the channel. Needs the protocol-3 engine.',
     {'track': _STRING, 'volume_db': {'type': 'number', 'minimum': -120, 'maximum': 6},
      'pan': {'type': 'number', 'minimum': -1, 'maximum': 1}, 'mute': {'type': 'boolean'}, 'solo': {'type': 'boolean'},
      'sends': {'type': 'array', 'maxItems': 16, 'items': {'type': 'object', 'additionalProperties': False,
                'properties': {'to': _STRING, 'level_db': {'type': 'number', 'minimum': -120, 'maximum': 6},
                               'remove': {'type': 'boolean'}}, 'required': ['to']}}}, ['track']),
    ('music_automation_write', 'Write an automation lane inside MAGDA (rendered with the song, unlike music_mixdown automation). target: {kind: volume|pan, track} | {kind: send, track, to} | {kind: param, device, param}. points: [{beat, value, curve?}] - volume in dB, pan -1..1, parameters in native range or as labels. Devices after the fader cannot be automated. Needs the protocol-3 engine.',
     {'target': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['volume', 'pan', 'send', 'param']},
                 'track': _STRING, 'to': _STRING, 'device': {'type': 'integer'}, 'param': {'type': ['string', 'integer']}},
                 'required': ['kind']},
      'points': {'type': 'array', 'maxItems': 4096, 'items': {'type': 'object', 'properties': {
          'beat': {'type': 'number', 'minimum': 0}, 'value': {'type': ['number', 'string']}, 'curve': {'type': 'number'}},
          'required': ['beat', 'value']}},
      'mode': {'type': 'string', 'enum': ['replace', 'add', 'range', 'clear'], 'default': 'replace'}}, ['target', 'points']),
    ('music_clip_edit', 'Arrangement edits at absolute beats: create (track, start, length, name?), move (clip, start, track?), resize (clip, length), duplicate (clip, start), rename (clip, name), delete (clip), loop (clip, loop_length, length). overlap: error (default) | replace | shift. Needs the protocol-3 engine.',
     {'action': {'type': 'string', 'enum': ['create', 'move', 'resize', 'duplicate', 'rename', 'delete', 'loop']},
      'clip': _STRING, 'track': _STRING, 'start': {'type': 'number', 'minimum': 0},
      'length': {'type': 'number', 'exclusiveMinimum': 0}, 'loop_length': {'type': 'number', 'exclusiveMinimum': 0},
      'name': _STRING, 'overlap': {'type': 'string', 'enum': ['error', 'replace', 'shift']}}, ['action']),
    ('music_stems_render', 'Render one WAV per track (default: every track feeding the master) into output_dir, for per-track loudness checks or stem exports. Needs the protocol-3 engine.',
     {'output_dir': _STRING, 'tracks': {'type': 'array', 'items': _STRING},
      'start_beats': {'type': 'number', 'minimum': 0}, 'length_beats': {'type': 'number', 'exclusiveMinimum': 0}},
     ['output_dir']),
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
    ('music_status', 'Active engine (real MAGDA or the fake emulator), binary, open project, workspace and engine features. Call first.', {}, []),
    ('music_project_new', 'Create and open a new MAGDA project: a name (created under the workspace) or an absolute .mgd path.',
     {'name': _STRING, 'path': _STRING, 'tempo_bpm': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 400}}, []),
    ('music_project_open', 'Open an existing .mgd project (or the folder that holds it).', {'path': _STRING}, ['path']),
    ('music_project_list', 'List .mgd projects in the workspace.', {}, []),
    ('music_project_save', 'Persist the open project to disk.', {}, []),
    ('music_notes_set', 'Replace ALL notes in a clip in one request: the fast path for writing a whole part. Starts are absolute beats; omit clip when the track has one clip.',
     {'track': _STRING, 'clip': _STRING, 'notes': {'type': 'array', 'maxItems': 20000, 'items': _note_param('one note')}}, ['track', 'notes']),
    ('music_parameter_describe', 'One parameter in full: range, unit, choices, current display text and, when the engine supports it, the plugin\'s value table.',
     {'track': _STRING, 'device': _STRING, 'parameter': _STRING}, ['track', 'device', 'parameter']),
    ('music_plugins_list', 'Scanned external plugins (VST3/AU/LV2) that can be hosted.',
     {'format': _STRING, 'instruments': {'type': 'boolean'}, 'query': _STRING}, []),
    ('music_plugin_add', 'Host an external plugin (instrument or effect) on a track by name or id, e.g. "Surge XT".',
     {'track': _STRING, 'plugin': _STRING, 'instrument': {'type': 'boolean'}}, ['track', 'plugin']),
]
_SPEC = {'type': 'object', 'description': 'the spec itself (paths inside it should be absolute)'}
_NEW_TOOLS += [
    ('music_device_catalog', 'Offline MAGDA device knowledge: every built-in instrument/effect (and Surge XT) with parameter names, ranges, units, defaults and labels. Give device for one device; omit it to list all. No engine needed.',
     {'device': _STRING, 'kind': {'type': 'string', 'enum': ['instrument', 'effect']},
      'live': {'type': 'boolean', 'description': 'also list what the running engine offers (protocol 3)'}}, []),
    ('music_presets', 'Verified track presets (instrument + settings + FX chain + fader + stem) for kicks, snares, hats, 808s, supersaws, pads, plucks, bells, leads, risers, noise. Give name for the full preset.',
     {'name': _STRING}, []),
    ('music_song_check', 'Preflight a song spec without the engine: presets, devices, every parameter (names, ranges, labels), notes vs clip length, stems, free disk space. Run before music_song_build.',
     {'spec': _SPEC, 'spec_path': _STRING, 'out_dir': _STRING}, []),
    ('music_song_build', 'Build a whole song spec in MAGDA (tracks, instruments, FX, levels, all notes) and render the mix plus one WAV per stem, with parameter read-back and a session report. Runs as a background job unless wait=true: poll music_job_status. Outputs: <out_dir>/mix.wav, stems/*.wav, score.json, session.json.',
     {'spec': _SPEC, 'spec_path': _STRING, 'out_dir': _STRING,
      'stems': {'type': ['boolean', 'array'], 'items': _STRING}, 'force': {'type': 'boolean'},
      'wait': {'type': 'boolean', 'default': False}}, []),
    ('music_balance', 'Balance a built song: render every track alone, measure its loudness per section and move each fader so the part sits at its level (LU relative to the kick; from the spec, else the preset). apply=true writes the faders and rebuilds stems/ and mix.wav from the track renders, so music_mixdown can run again at once. Background job unless wait=true. Needs the protocol-3 engine.',
     {'song_dir': _STRING, 'apply': {'type': 'boolean', 'default': False},
      'levels': {'type': 'object', 'additionalProperties': {'type': 'number', 'minimum': -40, 'maximum': 12}},
      'reference_lufs': {'type': 'number', 'minimum': -40, 'maximum': -6},
      'wait': {'type': 'boolean', 'default': False}}, ['song_dir']),
    ('music_job_status', 'Status, recent log lines and (when done) the result of a background job; omit job_id to list jobs.',
     {'job_id': _STRING, 'full': {'type': 'boolean', 'default': True}}, []),
    ('music_mixdown', 'Mix and master rendered stems from a mix spec: volume/filter automation, sidechain ducking keyed from the score, cuts, tape stop / stutter / reverse swell / telephone edits, bus EQ + glue, loudness target with a true-peak limiter. Writes master WAV/MP3, spectrogram and a report with per-section loudness. Times can be beats or section references like "pre1.end-1".',
     {'spec': _SPEC, 'spec_path': _STRING, 'out_dir': _STRING, 'wait': {'type': 'boolean', 'default': True}}, []),
    ('music_score_check', 'Symbolic checks on a score.json before rendering: semitone clashes against the chord track, notes outside part ranges, overlaps in mono parts, notes per bar per section.',
     {'score': {'type': 'object'}, 'score_path': _STRING,
      'ranges': {'type': 'object', 'additionalProperties': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 2, 'maxItems': 2}},
      'mono': {'type': 'array', 'items': _STRING}}, []),
    ('music_section_report', 'Measure a WAV (and optionally its stems) per section: LUFS, peak, crest, spectral centroid, width and band balance; flags a flat loudness contour. Section boundaries come from a score.json.',
     {'path': _STRING, 'score_path': _STRING, 'stems_dir': _STRING}, ['path']),
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
    # Programmatic default stays the emulator (tests and embedders rely on it);
    # the `python -m composerbench.mcp.server` entry point defaults to "auto",
    # which picks the persistent MAGDA engine whenever a binary is found.
    adapter: str = "fake"          # auto | magda_serve | magda | fake
    cli_path: str = ""
    project_path: str = ""
    serve: bool = False
    workspace: str = ""


def effective_adapter(cfg: ServerConfig) -> str:
    if cfg.adapter != "auto":
        return cfg.adapter
    return "magda_serve" if discover_cli(cfg.cli_path) else "fake"


def normalize_project_path(path: str | Path) -> Path:
    """Accept foo.mgd, the <dir>/<stem>/<stem>.mgd wrapper MAGDA writes, or a folder."""
    p = Path(path).expanduser()
    if p.is_dir():
        found = sorted(p.glob("*.mgd")) or sorted(p.glob("*/*.mgd"))
        if len(found) != 1:
            raise ValueError(f"{p} holds {len(found)} .mgd projects; pass the file itself")
        return found[0]
    if p.suffix != ".mgd":
        p = p.with_suffix(".mgd")
    nested = p.parent / p.stem / p.name
    return nested if not p.exists() and nested.exists() else p


def build_client(cfg: ServerConfig, project_path: str | Path | None = None) -> ComposerBench:
    adapter = effective_adapter(cfg)
    project = project_path or cfg.project_path
    if adapter in ("magda", "magda_serve"):
        cli = discover_cli(cfg.cli_path)
        if cli is None:
            raise MagdaCliNotFound(searched=[cfg.cli_path, *CLI_CANDIDATES],
                                  hint="build magda_cli or set COMPOSERBENCH_CLI")
        if not project:
            raise NoProject(hint="call music_project_new or music_project_open")
        return ComposerBench.connect_magda(cli, normalize_project_path(project),
                                        serve=cfg.serve or adapter == "magda_serve")
    if adapter != "fake":
        raise ValueError(f"unknown adapter {cfg.adapter}")
    if not project:
        return ComposerBench.connect_fake(cfg.workspace or ".")
    root = Path(project).expanduser()
    # The emulator keeps its state in a folder: a .mgd path means "its folder".
    return ComposerBench.connect_fake(root.parent if root.suffix == ".mgd" else root)


class _NoProject:
    """Placeholder client until music_project_new/open is called."""

    def __getattr__(self, name):
        raise NoProject(hint="call music_project_new or music_project_open first")

    async def close(self):
        return None


class MCPServer:
    def __init__(self, client: ComposerBench | None, cfg: ServerConfig | None = None):
        import asyncio
        self.cfg = cfg or ServerConfig(adapter="fake")
        self.client = client if client is not None else _NoProject()
        self.project_path: str | None = str(getattr(getattr(client, "daw", None), "project", "")) or None
        self._call_lock = asyncio.Lock()
        from composerbench.jobs import JobManager
        self.jobs = JobManager()

    async def _switch_project(self, path: Path, create: bool) -> dict:
        new_client = build_client(self.cfg, path)
        if create:
            await new_client.initialize()
        old, self.client = self.client, new_client
        try:
            await old.close()
        except Exception as e:  # noqa: BLE001 - never let a dead engine block the switch
            log.warning("previous project did not close cleanly: %s", e)
        self.project_path = str(getattr(new_client.daw, "project", path))
        return {"project": self.project_path, **(await new_client.project()).model_dump()}

    # ---- JSON-RPC plumbing ------------------------------------------------------

    async def close(self) -> None:
        """Stop the open project's engine (if any); the server can be reused afterwards."""
        await self.client.close()
        self.client = _NoProject()
    def dispatch(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        req_id = request.get("id")
        if method == "initialize":
            return self._result(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return self._result(req_id, {})
        if method in ("notifications/initialized", "notifications/cancelled"):
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
        if isinstance(self.client, _NoProject):
            cli = discover_cli(self.cfg.cli_path)
            return self._tool_text({
                'project': None, 'adapter': effective_adapter(self.cfg),
                'cli': str(cli) if cli else None,
                'note': 'capabilities depend on the engine build and are reported once a project is open',
                'next': 'music_project_new or music_project_open'})
        return self._tool_text(self.client.capabilities())

    async def _tool_music_devices_inspect(self, args):
        return self._tool_text({'devices': [d.model_dump() for d in await self.client.devices(args['track'])]})

    async def _tool_music_parameters_inspect(self, args):
        return self._tool_text({'parameters': await self.client.parameters(args['track'], args['device'])})

    async def _tool_music_parameter_set(self, args):
        return self._tool_text(await self.client.set_parameter(**args))

    async def _tool_music_volume_set(self, args):
        return self._tool_text(await self.client.set_volume(**args))

    def _engine_p3(self):
        daw = self.client.daw
        if not hasattr(daw, 'set_mixer'):
            raise UnsupportedOperation(reason='needs the persistent MAGDA engine (serve protocol 3)',
                                       adapter=type(daw).__name__)
        return daw

    async def _tool_music_fx_add(self, args):
        if args.get('position') or args.get('sidechain_from') or str(args['track']).lower() == 'master':
            daw = self._engine_p3()
            position = args.get('position', 'pre')
            device = await daw.add_effect(args['track'], args['name'], position=position)
            out = {'device_id': device, 'position': position}
            if args.get('sidechain_from'):
                keyed = await daw.set_sidechain(device, args['sidechain_from'], args.get('sidechain_type', 'midi'))
                out['sidechain'] = keyed.get('sidechain')
            return self._tool_text(out)
        return self._tool_text(await self.client.add_fx(track=args['track'], name=args['name']))

    async def _tool_music_mixer_set(self, args):
        daw = self._engine_p3()
        channel = await daw.set_mixer(args['track'], volume_db=args.get('volume_db'), pan=args.get('pan'),
                                      mute=args.get('mute'), solo=args.get('solo'))
        for send in args.get('sends', []):
            channel = await daw.set_send(args['track'], send['to'], level_db=send.get('level_db'),
                                         remove=send.get('remove', False))
        return self._tool_text({'channel': channel})

    async def _tool_music_automation_write(self, args):
        lane = await self._engine_p3().write_automation(args['target'], args['points'], args.get('mode', 'replace'))
        return self._tool_text(lane)

    async def _tool_music_clip_edit(self, args):
        fields = {k: args[k] for k in ('start', 'length', 'name', 'overlap') if k in args}
        if 'loop_length' in args:
            fields['loopLength'] = args['loop_length']
        clip = await self._engine_p3().clip_edit(args['action'], clip=args.get('clip'), track=args.get('track'),
                                                 **fields)
        return self._tool_text({'clip': clip})

    async def _tool_music_stems_render(self, args):
        stems = await self._engine_p3().render_stems(args['output_dir'], args.get('tracks'),
                                                     args.get('start_beats'), args.get('length_beats'))
        return self._tool_text({'stems': stems})

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

    # ---- project management & fast paths ---------------------------------------
    def _workspace(self) -> Path:
        return Path(self.cfg.workspace or os.environ.get('COMPOSERBENCH_WORKSPACE') or DEFAULT_WORKSPACE).expanduser()

    async def _tool_music_status(self, args):
        cli = discover_cli(self.cfg.cli_path)
        adapter = effective_adapter(self.cfg)
        out = {'adapter': adapter, 'cli': str(cli) if cli else None,
               'project': self.project_path, 'workspace': str(self._workspace()),
               'server_version': SERVER_INFO['version']}
        if adapter == 'fake':
            out['warning'] = ('fake emulator: renders a test tone and cannot make music; '
                              'build magda_cli or set COMPOSERBENCH_CLI')
        if isinstance(self.client, _NoProject):
            out['next'] = 'music_project_new or music_project_open'
        else:
            out['features'] = await self.client.features()
        return self._tool_text(out)

    async def _tool_music_project_new(self, args):
        import re
        import time
        if args.get('path'):
            path = Path(args['path']).expanduser()
            path = path if path.suffix == '.mgd' else path.with_suffix('.mgd')
        else:
            name = args.get('name') or time.strftime('untitled-%Y%m%d-%H%M%S')
            safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', name).strip('._') or 'untitled'
            path = self._workspace() / safe / f'{safe}.mgd'
        if path.exists() or (path.parent / path.stem / path.name).exists():
            raise ProjectExists(path=str(path), hint='use music_project_open')
        path.parent.mkdir(parents=True, exist_ok=True)
        info = await self._switch_project(path, create=True)
        if args.get('tempo_bpm'):
            await self.client.batch([{'type': 'set_tempo', 'bpm': float(args['tempo_bpm'])}])
            info = {'project': self.project_path, **(await self.client.project()).model_dump()}
        return self._tool_text({'created': True, **info})

    async def _tool_music_project_open(self, args):
        path = normalize_project_path(args['path'])
        if not path.exists():
            raise ProjectNotFound(path=str(path), hint='music_project_list shows the workspace')
        return self._tool_text({'opened': True, **(await self._switch_project(path, create=False))})

    async def _tool_music_project_list(self, args):
        ws = self._workspace()
        found = sorted({str(p) for pattern in ('*.mgd', '*/*.mgd', '*/*/*.mgd') for p in ws.glob(pattern)
                        if '.musicbench' not in p.parts})
        return self._tool_text({'workspace': str(ws), 'projects': found})

    async def _tool_music_project_save(self, args):
        return self._tool_text(await self.client.save())

    async def _tool_music_notes_set(self, args):
        from composerbench.adapter.magda import parse_note_pitch
        notes = [Note(start=float(n['start']), duration=float(n.get('duration', 1.0)),
                      pitch=parse_note_pitch(n['pitch']), velocity=int(n.get('velocity', 100)))
                 for n in args['notes']]
        count = await self.client.set_notes(args['track'], clip=args.get('clip'), notes=notes)
        return self._tool_text({'notes': count})

    async def _tool_music_parameter_describe(self, args):
        return self._tool_text(await self.client.describe_parameter(
            args['track'], args['device'], args['parameter']))

    async def _tool_music_plugins_list(self, args):
        plugs = await self.client.plugins(args.get('format'), args.get('instruments'))
        query = (args.get('query') or '').lower()
        rows = [{'id': p.get('id'), 'name': p.get('name'), 'format': p.get('format'),
                 'instrument': bool(p.get('isInstrument'))}
                for p in plugs if query in str(p.get('name', '')).lower()]
        return self._tool_text({'plugins': rows[:300], 'total': len(rows)})

    async def _tool_music_plugin_add(self, args):
        return self._tool_text(await self.client.add_plugin(
            args['track'], args['plugin'], args.get('instrument')))


    # ---- production layer ------------------------------------------------------
    @staticmethod
    def _spec_source(args: dict, what: str):
        if args.get('spec') is not None:
            return args['spec']
        if args.get('spec_path'):
            return args['spec_path']
        raise ValueError(f"give '{what}' as 'spec' (object) or 'spec_path' (JSON file)")

    async def _tool_music_device_catalog(self, args):
        from composerbench.devices import catalog
        if args.get('device'):
            entry = catalog.device(args['device'])
            if entry is None:
                raise KeyError(f"{args['device']!r} is not in the catalog; "
                               f"known: {[d['name'] for d in catalog.list_devices()]}")
            return self._tool_text({**entry, 'text': catalog.describe(args['device'])})
        out = {'devices': catalog.list_devices(args.get('kind'))}
        if args.get('live'):
            out['engine'] = await self._engine_p3().engine_catalog()
        return self._tool_text(out)

    async def _tool_music_presets(self, args):
        from composerbench.devices.presets import get_preset, list_presets
        if args.get('name'):
            return self._tool_text({'name': args['name'], **get_preset(args['name'])})
        return self._tool_text({'presets': list_presets()})

    async def _tool_music_song_check(self, args):
        from composerbench.song.build import check_song
        return self._tool_text(check_song(self._spec_source(args, 'spec'), args.get('out_dir')))

    async def _tool_music_song_build(self, args):
        from composerbench.song.build import build_song
        source = self._spec_source(args, 'spec')
        cli = discover_cli(self.cfg.cli_path)
        if cli is None:
            raise MagdaCliNotFound(searched=[self.cfg.cli_path, *CLI_CANDIDATES],
                                   hint="song builds need the real engine; set COMPOSERBENCH_CLI")

        def summary(r):
            return {k: r.get(k) for k in ('title', 'out_dir', 'renders', 'problems', 'readback_mismatch',
                                          'timings')}

        async def work(job):
            report = await build_song(source, out_dir=args.get('out_dir'), cli_path=cli,
                                      progress=job.progress, force=bool(args.get('force')),
                                      stems=args.get('stems'))
            return summary(report)
        title = source.get('title', '?') if isinstance(source, dict) else str(source)
        job = self.jobs.start('song', f'build {title}', work)
        if args.get('wait'):
            await job.task
            return self._tool_text(job.to_dict(full=True), is_error=job.status != 'done')
        return self._tool_text({**job.to_dict(), 'next': 'poll music_job_status with this job_id'})

    async def _tool_music_balance(self, args):
        from composerbench.mixing.balance import balance_song
        cli = discover_cli(self.cfg.cli_path)
        if cli is None:
            raise MagdaCliNotFound(searched=[self.cfg.cli_path, *CLI_CANDIDATES],
                                   hint="the balance pass renders tracks; set COMPOSERBENCH_CLI")

        async def work(job):
            report = await balance_song(args['song_dir'], cli_path=cli, apply=bool(args.get('apply')),
                                        reference_lufs=args.get('reference_lufs'), levels=args.get('levels'),
                                        progress=job.progress)
            return {**{k: report.get(k) for k in ('song', 'reference_lufs', 'reference_shift_db', 'applied',
                                                  'peak_dbfs', 'spec_patch')},
                    'tracks': [{k: r.get(k) for k in ('track', 'volume', 'new_volume', 'gain_db', 'measured_lufs',
                                                      'target_lufs', 'note')} for r in report['tracks']]}
        job = self.jobs.start('balance', f"balance {args['song_dir']}", work)
        if args.get('wait'):
            await job.task
            return self._tool_text(job.to_dict(full=True), is_error=job.status != 'done')
        return self._tool_text({**job.to_dict(), 'next': 'poll music_job_status with this job_id'})

    async def _tool_music_job_status(self, args):
        if not args.get('job_id'):
            return self._tool_text({'jobs': self.jobs.list()})
        job = self.jobs.get(args['job_id'])
        return self._tool_text(job.to_dict(full=args.get('full', True)), is_error=job.status == 'failed')

    async def _tool_music_mixdown(self, args):
        import asyncio

        from composerbench.mixing.mixdown import run_mix
        source = self._spec_source(args, 'spec')

        def summarize(r):
            out = {k: r.get(k) for k in ('files', 'master', 'premaster', 'applied', 'notes', 'bus',
                                         'seconds_to_mix')}
            if r.get('sections'):
                out['section_lufs'] = r['sections']['contour']
                out['flags'] = r['sections']['flags']
            return out

        async def work(job):
            job.progress('mixing', {})
            return summarize(await asyncio.to_thread(run_mix, source, args.get('out_dir')))
        job = self.jobs.start('mix', 'mixdown', work)
        if args.get('wait', True):
            await job.task
            return self._tool_text(job.to_dict(full=True), is_error=job.status != 'done')
        return self._tool_text({**job.to_dict(), 'next': 'poll music_job_status with this job_id'})

    async def _tool_music_score_check(self, args):
        from composerbench.analysis.harmony import check_score
        from composerbench.composition.score import Score
        if args.get('score') is not None:
            score = Score.from_dict(args['score'])
        elif args.get('score_path'):
            score = Score.load(args['score_path'])
        else:
            raise ValueError("give 'score' (object) or 'score_path'")
        ranges = {k: tuple(v) for k, v in (args.get('ranges') or {}).items()}
        return self._tool_text(check_score(score, ranges, args.get('mono')))

    async def _tool_music_section_report(self, args):
        import asyncio
        import json as _json

        from composerbench.analysis.sections import section_report
        from composerbench.analysis.wav import read_wav
        from composerbench.mixing.dsp import as_stereo
        from composerbench.mixing.loudness import loudness_summary

        def work():
            x, sr = read_wav(args['path'])
            x = as_stereo(x)
            sections = []
            if args.get('score_path'):
                sc = _json.loads(Path(args['score_path']).read_text())
                k = 60.0 / sc['bpm']
                sections = [{'name': s['name'], 'start_s': s['start'] * k, 'end_s': s['end'] * k}
                            for s in sc.get('sections', [])]
            stems = {}
            if args.get('stems_dir'):
                for p in sorted(Path(args['stems_dir']).glob('*.wav')):
                    y, _ = read_wav(p)
                    stems[p.stem] = as_stereo(y)
            return {'summary': loudness_summary(x, sr),
                    'sections': section_report(x, sr, sections, stems) if sections else None}
        return self._tool_text(await asyncio.to_thread(work))


def config_from_env(argv: list[str] | None = None) -> ServerConfig:
    import argparse
    ap = argparse.ArgumentParser(prog='python -m composerbench.mcp.server')
    ap.add_argument('--adapter', default=os.environ.get('COMPOSERBENCH_ADAPTER', 'auto'),
                    choices=['auto', 'magda_serve', 'magda', 'fake'])
    ap.add_argument('--cli', default=os.environ.get('COMPOSERBENCH_CLI', ''))
    ap.add_argument('--project', default=os.environ.get('COMPOSERBENCH_PROJECT', ''))
    ap.add_argument('--workspace', default=os.environ.get('COMPOSERBENCH_WORKSPACE', ''))
    ns = ap.parse_args(argv)
    serve_env = os.environ.get('COMPOSERBENCH_SERVE', '').lower()
    return ServerConfig(adapter=ns.adapter, cli_path=ns.cli, project_path=ns.project,
                        workspace=ns.workspace, serve=serve_env not in ('0', 'false'))


def serve_stdio(cfg: ServerConfig | None = None) -> int:
    import asyncio
    cfg = cfg or config_from_env([])
    # The protocol owns stdout. Anything else that prints (engine chatter,
    # library logging, stray print calls) is redirected to stderr so it can
    # never corrupt a JSON-RPC frame.
    protocol_out = sys.stdout
    sys.stdout = sys.stderr
    client = None
    if cfg.project_path or effective_adapter(cfg) == 'fake':
        client = build_client(cfg)
    server = MCPServer(client, cfg)

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
                    protocol_out.write(json.dumps(response) + '\n')
                    protocol_out.flush()
        finally:
            await server.client.close()
    asyncio.run(main())
    return 0


def main(argv: list[str] | None = None) -> int:
    return serve_stdio(config_from_env(argv if argv is not None else sys.argv[1:]))


if __name__ == '__main__':
    raise SystemExit(main())
