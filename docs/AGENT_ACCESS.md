# Agent access: MCP setup, fast note writing, plugins and parameter meaning

For whole songs, see [Agentic production](AGENTIC_PRODUCTION.md). It covers
score helpers, the device catalog and presets, one-call song builds with stem
renders, and the mix/master stage.

This page covers the four changes that make ComposerBench practical to drive from
an agent (Claude, Cursor, or anything that speaks MCP):

1. one-step registration of the MCP server, using the persistent engine;
2. whole-clip note writing and one save per batch;
3. hosted plugins (Surge XT, Dragonfly, ...) that are saved with the project;
4. parameters that say what they mean: labels in, labels out.

Items 2-4 need MAGDA's **serve protocol 2**. ComposerBench detects the engine's
protocol when a session starts and falls back to protocol-1 behaviour against
older builds, so everything below also works (more slowly, with the limits
noted) on an unmodified `magda_cli`.

## 1. Connect an agent

```sh
composerbench mcp install                    # Claude Desktop, Claude Code and Cursor
composerbench mcp install --client cursor    # just one client (repeatable)
composerbench mcp install --dry-run          # show what would change, write nothing
composerbench mcp doctor                     # start the server as a client would and call it
composerbench mcp config                     # print the server entry only
```

`install` finds `magda_cli` (`--cli`, `$COMPOSERBENCH_CLI`, then the usual
`~/magda-core` build folders) and writes one `mcpServers.musicbench` entry:

```json
{
  "command": "/Users/you/composerbench/.venv/bin/python",
  "args": ["-m", "composerbench.mcp.server"],
  "env": {
    "COMPOSERBENCH_ADAPTER": "magda_serve",
    "COMPOSERBENCH_CLI": "/Users/you/magda-core/.../magda_cli",
    "COMPOSERBENCH_WORKSPACE": "/Users/you/Music/ComposerBench/projects",
    "PYTHONPATH": "/Users/you/composerbench",
    "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:..."
  }
}
```

| Client | Where it goes |
| --- | --- |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) |
| Cursor | `~/.cursor/mcp.json` |
| Claude Code | `claude mcp add-json --scope user composerbench '<entry>'` (skipped, with the command printed, when the `claude` CLI is not on PATH) |

Existing configuration is merged, never replaced: other servers are kept, the
previous file is copied to `<file>.bak-<timestamp>`, and running the command
again is a no-op. Restart the client afterwards.

The server no longer needs a project up front. An agent starts with
`music_status`, then `music_project_new` (created under the workspace) or
`music_project_open` (a `.mgd`, its folder, or MAGDA's `<name>/<name>.mgd`
wrapper). `COMPOSERBENCH_PROJECT` still pre-opens one. The adapter defaults to
the persistent engine (`magda_serve`): one engine boot per session instead of
one per command. Without a MAGDA binary it reports `fake` in `music_status`
and warns that the emulator cannot make music.

### Tools added for agents

| Tool | Purpose |
| --- | --- |
| `music_status` | adapter, binary, workspace, open project, engine protocol features |
| `music_project_new` / `_open` / `_list` / `_save` | project lifecycle without restarting the server |
| `music_notes_set` | replace a clip's notes in one call (up to 20,000 notes, pitches as numbers or names like `"C#4"`) |
| `music_parameter_describe` | range, unit, default, labels and the current display text of one parameter |
| `music_plugins_list` / `music_plugin_add` | scanned AU/VST3 plugins; host one on a track |

`music_parameter_set` now accepts a number or a label (`"Hall"`, `"1/8."`,
`"LP 12 dB"`). The server's `instructions` describe the intended workflow.

## 2. Writing notes

Write a whole clip at once:

```python
await mb.set_notes("lead", clip="verse", notes=[Note(start=0, duration=0.5, pitch=60, velocity=96), ...])
```

or `{"type": "set_notes", ...}` inside `mb.batch([...])`. On protocol 2 that is
one `{"op":"notes","mode":"replace"}` request: one undo step, one engine sync,
the clip keeps its id and name. `add_notes` uses the same request with
`"mode":"add"`. Older engines get a single `exec` with every note (and, for
`set_notes`, the clip is rebuilt under the same name).

Saving: `mb.batch()` switches the engine's per-command save off for its
duration and saves once at commit, instead of after every command. Direct
adapter users can do the same with `daw.autosave = False` and
`await daw.save_project()`. Dumps are cached until the next edit.

`examples/bench_note_writing.py` measures the three write paths on the real
engine; `tests/test_p2_magda_live.py` checks them (skipped without a binary).

## 3. Hosted plugins persist

On protocol 2, `add_plugin` (and `music_plugin_add`, and `add_fx` with a plugin
id) hosts the plugin inside MAGDA's device model: it is listed in the dump
with its parameters, saved in the `.mgd` together with the plugin's own state,
reloaded with the project and kept by snapshot restore. Results carry
`"persistent": true`.

Older engines host plugins beside the model. ComposerBench still lists them as
devices for the rest of the session, flagged `"persistent": false` with a
note, so an agent can tell that a restart or snapshot restore will drop them.

## 4. Parameter meaning

Every parameter entry (`music_parameters_inspect`, `get_parameters`) can carry:

| Field | Meaning |
| --- | --- |
| `choices` | labels of a discrete parameter, in order |
| `choiceValues` | where each label sits, when the engine sampled the plugin |
| `text` | the display text of the current value ("Hall", "LP 12 dB", "523 Hz") |
| `steps`, `default`, `unit`, `scale` | as reported by the engine |
| `owner: "host"` | the engine's wrapper parameter, not the plugin's own |

Labels resolve exactly, ignoring case and spaces, or by unique prefix; an
unknown label fails with the list of valid ones, and numbers outside the
range fail with the range. Some hosted plugins (Surge XT among them) do not
declare their discrete parameters, so their labels are fetched on demand with
`{"op":"param_info"}` when a label is used or described.

Tracktion puts a **Dry Level / Wet Level** pair in front of every hosted
plugin's own parameters. Those two scale the whole plugin output; they are not
the plugin's mix. They are now marked `owner: "host"` and addressed as
`host:Dry Level` / `host:Wet Level`, while plain `"Dry Level"` means the
plugin's own control (Dragonfly has both). Old engines address hosted-plugin
parameters by name only; ComposerBench refuses an ambiguous name there rather
than silently moving the wrapper.

### Role recipes and learned values

`ParamSpec` in `~/.musicbench/semantics/*.yaml` takes a `label`, a `value`, or
both (the value is then the fallback for engines that cannot confirm labels):

```yaml
warm_pad:
  engine: surge_xt
  params:
    - {names: [A Filter 1 Type, Filter 1 Type], label: LP 12 dB, value: 0.03}
    - {names: [A Filter 1 Cutoff, Cutoff, cutoff, Filter Cutoff], value: 0.58}
```

Candidate names are tried in order, exact matches before substring matches,
and the plugin's own parameters before the wrapper's. `apply_role` reports each
parameter with how it was set (`label`, `value`, or `value-fallback`) and the
resulting display text.

Surge XT's init patch leaves Filter 1 **Off**, so the old `warm_pad` and
`sub_bass` cutoffs never changed the sound, and the learned `warm_pad` cutoff
(0.6) was measured against a filter that was off. The recipes now switch the
filter to LP 12 dB first; the learned file is marked `"invalid": true`.
`SemanticRegistry.merge_learned` skips invalid files and any learned value
whose recorded `context` (the recipe's other parameters at learning time) no
longer matches the recipe; skipped files are listed in `skipped_learned`. The
sweep harness now reproduces that context before sweeping and records it.

`reverb_wet` in a role still drives the reverb's wrapper Wet Level (a gain on
the reverb's whole output, as before), so existing songs keep their balance.

## Serve protocol 2 reference

`{"op":"describe"}` → `{"ok":true,"protocol":2,"features":{...}}`. A server
that does not know `describe` is protocol 1.

| Feature | Request | Effect |
| --- | --- | --- |
| `execSaveFlag` | `{"op":"exec","commands":[...],"save":false}` | dispatch without saving |
| `bulkNotes` | `{"op":"notes","clipId":N,"mode":"add"\|"replace","notes":[{"start","length","pitch","velocity"}],"save":bool}` | clip-relative starts; one undo step; replies `added`, `removed`, `total` |
| `paramLabels` | `dump` | parameters carry `choices`, `text`, `steps`, `default`, wrapper params `owner:"host"` |
| `paramInfo` | `{"op":"param_info","deviceId":N,"param":name\|index}` | `text`, `choices`, `choiceValues`, `steps`, `valueTable`, `default` |
| `pluginPersistence` | `add_plugin` | model-hosted plugin, saved with state; reply has `"persistent":true` |
| `extParamIndex` | `{"op":"set_ext_param",...,"index":i}` | address a hosted parameter by index |

Consecutive `add-midi-note` commands for one clip inside an `exec` are also
coalesced into a single undo step on protocol-2 builds.

## Serve protocol 3 reference

Protocol 3 adds engine-side mixing. `describe` reports `"protocol": 3` and these
feature flags: `mixer`, `sends`, `sidechain`, `automation`, `clipOps`,
`clipOverlapPolicy`, `clipLoop`, `stems`, `masterBus`, `catalog`, `commandList`.

| Request | Effect |
| --- | --- |
| `{"op":"mixer","trackId":N\|"master","volume"\|"volumeDb","pan","mute","solo","name"}` | channel settings; replies with the channel (`volumeDb`, `sends`, ...) |
| `{"op":"send","from","to","level"\|"levelDb","remove"?}` | post-fader send to an aux/group track; feedback loops are refused |
| `{"op":"add_fx","trackId"\|"master","plugin","position":"pre"\|"post"}` | insert a built-in effect (master bus included) |
| `{"op":"sidechain","deviceId","source"\|null,"type":"midi"\|"audio"}` | key a pre-fader device from another track |
| `{"op":"automation","target":{"kind":"volume"\|"pan"\|"send"\|"param",...},"points":[{"beat","value","curve"?}],"mode"}` | a lane in real units (dB, -1..1, native range or labels) |
| `{"op":"clip","action":"create"\|"move"\|"resize"\|"duplicate"\|"rename"\|"delete"\|"loop",...,"overlap"}` | arrangement edits at absolute beats |
| `{"op":"render","wav","tracks":[ids]}` | render only those tracks (post-fader); an empty list means every track, so ComposerBench never sends one |
| `{"op":"render_stems","dir","tracks"?}` | one WAV per channel |
| `{"op":"catalog"}` | built-in instruments and effects |

On the Python side these are `set_mixer`, `set_send`, `add_effect`,
`set_sidechain`, `write_automation`, `clip_edit`, `render_stems` and
`engine_catalog` on `MagdaServeAdapter`; over MCP `music_mixer_set`,
`music_fx_add` (master / position / sidechain_from), `music_automation_write`,
`music_clip_edit`, `music_stems_render` and `music_device_catalog` with
`live: true`. `examples/p3_engine_smoke.py` exercises every op on a real
engine (36 checks).

## Toolsets

`COMPOSERBENCH_TOOLSETS` (or `composerbench mcp install --toolsets ...`) limits
the tools a client sees: `core` (status, projects, jobs - always on), `song`
(catalog, presets, score/song checks, build, balance, mixdown, section report),
`engine` (tracks, devices, notes, parameters, mixer, automation, clips, renders),
`audio` (inspect, compare, resample, visual review) and `revision` (intent,
plan, snapshots, roles, batches, measured revisions). The default is `all`.
A whole-song agent does well with `song,audio`.
