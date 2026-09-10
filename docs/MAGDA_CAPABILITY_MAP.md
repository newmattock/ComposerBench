# MAGDA Capability Map

Status: **LIVE-VERIFIED** — integration claims exercised against the built binary (`magda_cli`, Debug, JUCE 8.0.12 / tracktion_engine @ 09f2748f).
Upstream: `github.com/Conceptual-Machines/magda-core` @ `ebd93db0`.
License: **GPL-3.0** (repo). Tracktion Engine/JUCE licensing still to be documented (§5).
Audit method: source inspection + live CLI smoke tests + automated ComposerBench integration suite (5/5 passing).

## Live-verified facts (first binary run, 2026-08-23)

- `boot` initializes full headless engine; AU/VST3/LV2 registered with out-of-process scanning.
- `init <dir>/proj.mgd` wraps to `<dir>/proj/proj.mgd` (wrapper folder confirmed live).
- Batched `exec` reliable across processes: set-tempo → add-track → add-midi-clip → add-midi-note ×2 → dump --json all persisted exactly as dispatched.
- `dump --json` matches `remote_api.hpp` DTOs; pitch key is **"note"** (adapter accepts both).
- **Silence without an instrument**: MIDI renders at −120 dBFS until a device loads — audibility gate confirmed.
- `add-internal-instrument <tid> 4osc <name>` loads FourOsc; render produced **peak −8.29 dBFS / RMS −21.25 dBFS** synthesized bass.
- `render --wav X --bit-depth 16 [--from/--to]`: duration exact to tempo math (8 beats @96 BPM = 5.000 s); section renders work.
- Transpose + snapshot/restore roundtrip verified on real files (36/43 → 41/48 → restored).

## External entry points discovered (the big three)

| Surface | What it is | Evidence |
|---|---|---|
| **Headless CLI** (`magda/daw/cli/`) | `boot / init / run / exec / render` against real `.mgd` projects with headless engine (`createDefaultAudioEngine({.headless=true})`) | `magda_cli_main.cpp` (1040 lines) |
| **Typed remote-API layer** (`magda/daw/api/remote_api.*`) | JSON DTOs (Project/Track/Clip/Device/Rack/Chain/Session/Automation/Transport/Selection), `OperationRegistry` with input/output schemas + validation + error envelopes (`InvalidRequest/UnknownOperation/ValidationFailed/NotFound/Conflict/InternalError`) | source ✅ |
| **In-app DSL** (`magda/agents/dsl_grammar.hpp`, `dsl_interpreter.cpp`) | Functional DAW language: `project.set`, `track()`, `.clip.new`, `.notes.add/add_chord/add_arpeggio/select/transpose/quantize`, `fx.add`, `rack.*`, `filter(tracks)`, `groove.*` | manual + source ✅ |

Also present: Lua 5.4 controller scripting (`magda/scripting/`) — sandboxed `magda.*` API covering transport/tracks/clips/session/focused-device macros; **no note-level editing in v0.7 Lua**. MAGDA's MCP support is as a *client* (spawning external MCP servers over stdio, `agents/mcp/MCPClient.*`) — not an MCP server.

## CLI command surface (verified from CommandDispatcher::commandSpecs)

```text
boot                                  # headless engine boot test
init <file.mgd>                       # create new project
run <file.mgd> [--out X] [--cmds f] [--dump-json]   # load→command-file→save roundtrip
exec <file.mgd> [cmd tokens...] [--out X] [--dump-json]  # load→dispatch→save
render <file.mgd> <out.wav> --from T1 --to T2 ...     # offline WAV render
```

Dispatch commands: `set-tempo`, `add-track <audio|group|aux|chord> [name]`,
`add-internal-instrument <track-id> <plugin-id> [name]`, `delete-track`,
`set-track-input`, `group-track`, `ungroup-track`, `route-midi-to`,
`add-midi-clip <track-id> <start-beats> <length-beats>`,
`delete-clip`, `add-midi-note <clip-id> <start-beats> <note> <length-beats> [velocity]`,
`quantize-notes`, `slice-notes`, `transpose-midi-clip`, `dump --json`.

Render time parser accepts musical units: `40bar(s)`, `16beat(s)`, seconds → **section rendering exists upstream** (spec §27 satisfied at Level 1).

## Capability matrix

Legend: supported / partial / internal-only / not-supported / unknown · access path in brackets.

### Queries

| Capability | Status | Access path |
|---|---|---|
| Project querying | ✅ supported (tempo, timesig, key, loop) | remote DTO + CLI `dump --json` |
| Track querying | ✅ supported | `TrackDto`, CLI dump |
| Clip querying | ✅ supported (incl. notes inside ClipDto) | `ClipDto.notes[]`, CLI dump |
| MIDI note querying | ✅ supported via clip projection | remote DTO; DSL `notes.select` |
| Audio clip querying | ❓ unknown (ClipDto.type exists) | remote DTO |
| Device querying | ✅ supported (DeviceGraphDto: devices/racks/chains) | remote DTO |
| Plugin parameter querying | ❓ partial (DeviceInfo has gainDb; parameter list TBD) | plugin_api |
| Automation querying | ✅ AutomationLaneDto w/ points + targets | remote DTO |
| Transport querying | ✅ TransportDto | remote DTO |

### Edits

| Capability | Status | Access path |
|---|---|---|
| Track create/delete | ✅ | CLI `add-track`/`delete-track`; MagdaApi.tracks() |
| Clip create/delete | ✅ MIDI clips | CLI `add-midi-clip`; audio clips ❓ |
| MIDI note add/edit | ✅ add/transpose/quantize/slice | CLI dispatch; DSL richer (chords/arps/select) |
| Note velocity/pitch edit | ✅ DSL `.notes.set_velocity/set_pitch` (in-app); ❓ CLI lacks set-velocity | DSL |
| Device/plugin loading | ✅ internal instruments via CLI; VST3/AU via DSL `fx.add(format=)` | both |
| Parameter modification | ✅ in-app (plugin_api_live); ❓ not in CLI dispatcher | C++ api |
| Automation editing | ✅ AutomationApi + lanes/points DTOs | C++ api; DSL partial |
| Routing modification | ✅ `set-track-input`, `route-midi-to`, racks/chains | CLI + C++ |

### Safety / persistence

| Capability | Status | Access path |
|---|---|---|
| Project save | ✅ every `exec/run` saves (`saveProjectForCli`) | CLI |
| Render/export WAV | ✅ offline render w/ range + samplerate/bitdepth opts | CLI `render` |
| Undo/redo | ✅ undo_api + `undo_api_live` | C++ api; ❓ not exposed in CLI |
| Transactions | ❌ none observed → ComposerBench-level snapshots required (spec §25) | — |
| Stable entity IDs | ⚠️ numeric TrackId/ClipId per session; ordering not guaranteed across loads → ComposerBench name-map layer needed (spec §11) | — |

## Integration decision (smallest path, spec §113)

**Level 1–2 achieved without forking:** ComposerBench drives the *unmodified* headless CLI:

```text
composerbench action ──▶ magda-cli init/exec/render on project .mgd ──▶ WAV + dump-json
```

- `exec` = atomic batched edit batch (load → N commands → save) ≈ poor-man's transaction.
- `dump --json` = hierarchical inspection source (maps to adapter queries).
- `render --from/--to` = section/stem renders.
- Persistent-session bridge (WebSocket/stdio server over OperationRegistry) = candidate **Level 3/4 upstream contribution**, deferred until dogfooding proves the per-process cost matters.

## Open questions — RESOLVED (source-verified)

1. **OperationRegistry surface** (`remote_api.cpp`): 20 operations — `system.describe`,
   `project.get`, `tracks.{list,get,create,update,delete}`, `clips.{list,get,delete}`,
   `devices.list`, `racks.{create,remove}`, `selection.{get,set}`,
   `transport.{get,play,stop,seek}`, `session.get`.
   **Notably absent**: `clips.create`, note editing, parameter writes, render, undo.
   ⇒ Note editing remains CLI-dispatcher/DSL territory; render is CLI-only. Confirms
   CLI-as-transport choice for v0.
2. **Undo exposure**: no undo commands in CLI dispatcher, none in OperationRegistry
   (undo_api exists as C++ layer only). ⇒ ComposerBench **file-level snapshots are THE
   transaction mechanism** (spec §25 Level 2). Upstream contribution candidate (Level 4).
3. **`.mgd` format**: `saveProjectAs` wraps output as `<dir>/<Stem>/<Stem>.mgd` plus a
   `<Stem>_Media/` folder for durable media; CLI prints `Saved <actual path>`.
   ⇒ Snapshots must capture mgd+media pair; adapter trusts the stdout line, not the
   requested path. Implemented in MagdaCliAdapter.
4. **Internal instrument names**: built-in devices expose canonical aliases from code
   (`@eq`, `@reverb`, …) via `core/aliases/InternalPluginAliases.hpp` +
   `AliasRegistry`; third-party plugins alias via JSON loader. `add-internal-instrument`
   takes the plugin id — exact registry enumeration pending first live run (plugin scan).
5. **Section render**: confirmed — `render --from/--to` parse `<n>bar(s)` / `<n>beat(s)` /
   seconds (verified in `parseRenderTime`).

## Level-3 extension sketch (parameter control, spec §8/§111)

CLI dump exposes **no device graph** (verified live: track keys lack `devices`), and
`OperationRegistry` has no transport. The narrow upstream-friendly bridge is:

1. `magda-cli serve <p.mgd>` — stdio JSON-RPC loop dispatching through `remote_api`
   operations (the required message-thread hop is already specified in remote_api.hpp).
2. Extend the registry with `clips.create`, `notes.add`, `devices.parameters.get/set`,
   `undo/redo` — all backed by existing live APIs
   (clip_api/midi_api/plugin_api/undo_api); no engine changes.
3. ComposerBench then swaps transport from batched `exec` to the persistent session:
   sub-second edits, true transactions (§25), and parameter semantics for M4/M8.

## Integration decision (smallest path, spec §113)
