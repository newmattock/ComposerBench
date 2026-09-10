# ComposerBench

**Give an AI music agent a workspace it can inspect, edit, render, and revise.**

ComposerBench is a Python toolkit for music composition and production through the MAGDA DAW. It connects musical intent and arrangement plans to MIDI, instruments, effects, actual audio, and a recorded revision loop. Built by Jacob Hatchett; formerly MusicBench.

The central idea is to give agents feedback on what they made: render a candidate, measure it, compare it at matched loudness, and keep or roll back the edit. Objective measurements and a listener's musical judgment remain separate.

## What works today

- **DAW control:** tracks, clips, MIDI notes, instruments, effects, parameter discovery, and offline rendering through MAGDA's CLI or persistent server.
- **Reversible editing:** preflight validation, snapshots, transactional batches, rollback, stable `mb:` references, and recorded before/after state.
- **Musical structure:** intent, sections, motifs, transformations, role presets, and arrangement constraints.
- **Audio feedback:** waveform and spectrum views, RMS, LUFS and true peak, stereo/mono diagnostics, time-localized findings, and loudness-matched A/B exports.
- **Revision evidence:** explicit objectives, baseline/candidate renders, acceptance or rejection reasons, and persisted history.
- **Agent interfaces:** a Python client, command-line interface, and a stdio MCP server exposing semantic music tools.

This is an early developer toolkit, not a hosted song generator. An external agent supplies decisions; ComposerBench executes and evaluates them. The fake adapter is for development and tests, not musical production. The `benchmark` package is a placeholder: this release does not claim a standardized music benchmark or leaderboard.

## Install and try without a DAW

Python 3.11 or newer is required. Clone this repository, then:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,visual]'
composerbench --json --project ./work/demo status
python examples/quickstart.py --project ./work/quickstart
python -m pytest -q
```

The quickstart builds a small MIDI arrangement through the fake adapter and prints the resulting state. It does not produce a finished track.

## Real audio with MAGDA

Build or install [MAGDA](https://github.com/Conceptual-Machines/magda-core) separately. ComposerBench does not include its engine, plugins, samples, or binary. The adapter expects the CLI commands described in [the capability map](docs/MAGDA_CAPABILITY_MAP.md); persistent mode additionally requires `serve`. Some synth/effect features depend on your MAGDA build. Install FFmpeg on your PATH for LUFS/true-peak analysis, matched auditions, and pitched resampling.

```sh
python examples/sonic_workflow.py \
  --cli /absolute/path/to/magda_cli \
  --work ./work/sonic-example \
  --output ./outputs/sonic-example
```

This example renders a synth phrase, compares two filter settings at matched loudness, includes a gain-only comparison, and exports a chopped/resampled variation. See [the sonic workflow](docs/SONIC_WORKFLOW.md) for requirements and interpretation.

```python
from composerbench.client import ComposerBench

# Inside an async function; use a fresh path when initializing a project.
client = ComposerBench.connect_magda(cli_path, project_path, serve=True)
try:
    await client.initialize()
    await client.batch([
        {"type": "create_track", "name": "lead"},
        {"type": "load_device", "track": "lead", "name": "4osc"},
        {"type": "create_clip", "track": "lead", "name": "phrase",
         "start_beats": 0, "length_beats": 4},
        {"type": "add_notes", "track": "lead", "clip": "phrase",
         "notes": [{"start": 0, "duration": 1, "pitch": 60, "velocity": 90}]},
    ])
finally:
    await client.close()
```

## Connect an agent through MCP

Configure your MCP client to launch `python -m composerbench.mcp.server` with these environment variables:

```sh
COMPOSERBENCH_ADAPTER=magda_serve \
COMPOSERBENCH_CLI=/absolute/path/to/magda_cli \
COMPOSERBENCH_PROJECT=/absolute/path/to/project.mgd \
python -m composerbench.mcp.server
```

Start with `music_project_inspect` and `music_capabilities`. Inspect live instruments and parameters before edits. Use `music_batch_edit` for deliberate changes or `music_revision_run` with explicit objectives for evaluated candidates. The MCP tool names retain the `music_` prefix.

## Documentation

- [Editing, snapshots and revision workflow](docs/P0_AGENT_WORKFLOW.md)
- [Audio diagnostics, auditioning and resampling](docs/SONIC_WORKFLOW.md)
- [Visual audio review](docs/VISUAL_AUDIO_REVIEW.md)
- [MAGDA capability map](docs/MAGDA_CAPABILITY_MAP.md) — historical build observations; inspect your own binary
- [Rename and compatibility](docs/MIGRATION.md)
- [Original design brief](docs/COMPOSERBENCH_MASTER_BUILD_PROMPT.md) — includes future ambitions, not a feature checklist

Tests requiring MAGDA skip when its local binary is unavailable. Offline tests run without the DAW. Song examples beyond the quickstart and sonic workflow are experimental recipes and may require locally installed sample libraries or devices.

## License and dependencies

This initial source publication preserves the project's existing `Proprietary-until-license-audit` status; it does not grant an open-source license. See [LICENSING.md](LICENSING.md). MAGDA and other third-party software remain separately licensed and are not bundled here.
