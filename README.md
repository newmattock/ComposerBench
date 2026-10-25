# ComposerBench

[![tests](https://github.com/newmattock/ComposerBench/actions/workflows/tests.yml/badge.svg)](https://github.com/newmattock/ComposerBench/actions/workflows/tests.yml)

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
- **Whole-song production** (see `examples/afterglow.py` and `examples/glitterstatic.py`): declarative song specs (tracks from verified presets or explicit instruments, FX chains, parameters by name or label, notes from a score) built and rendered in MAGDA in one call, with a mix and one WAV per stem. A mix/master stage then adds automation, score-keyed sidechain, cuts, tape stop/stutter/reverse-swell edits, bus processing and a BS.1770 loudness target with a true-peak ceiling. See [agentic production](docs/AGENTIC_PRODUCTION.md).
- **Balance and checks that stand in for listening:** parts placed by loudness level relative to the kick (every preset measured on the engine), a balance pass that renders each track and moves its fader, and warnings for a bridge louder than its chorus, a bass-heavy or mono mix, buried stems, and notes that ring through sections.
- **Engine-side mixing (patched MAGDA, protocol 3):** mixer, sends, sidechain keys, automation lanes, clip edits, master-bus inserts and per-track renders.
- **Composition helpers and checks:** drum-pattern strings, chord voicings, arpeggios, legato slides, a score format with sections and a chord track, avoid-note and range checks, and an offline catalog of every MAGDA device parameter with ranges and labels.

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

Song specs, scores and mix specs can be written and checked without a DAW:

```sh
composerbench song example --score-out score.json > song.json   # 8-bar demo using built-in devices
composerbench song check song.json                              # devices, parameters, notes, disk space
composerbench presets                                           # verified track presets
composerbench devices magda_polysynth --text                    # parameter names, ranges and labels
```

## Real audio with MAGDA

Build [MAGDA](https://github.com/Conceptual-Machines/magda-core) separately with the ComposerBench serve patch: [building MAGDA](docs/BUILDING_MAGDA.md) has the exact commit and the steps for Linux and macOS. ComposerBench does not include its engine, plugins, samples, or binary, and finds `magda_cli` through `$COMPOSERBENCH_CLI` or the usual `~/magda-core` build folders. Some synth/effect features depend on your MAGDA build. Install FFmpeg on your PATH for LUFS/true-peak analysis, matched auditions, and pitched resampling.

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

Whole songs go through the song builder and the mix stage. `examples/glitterstatic.py` is a complete hyperpop instrumental written this way:

```sh
composerbench song build song.json --out ./outputs/demo     # MAGDA project, mix.wav, stems/*.wav, session.json
composerbench mix example > ./outputs/demo/mix.json
composerbench mix ./outputs/demo/mix.json                   # master WAV/MP3, spectrogram, loudness report
python examples/glitterstatic.py --out ./outputs/glitterstatic
```

## Connect an agent through MCP

`composerbench mcp install` registers the server with Claude Desktop, Claude Code and Cursor ([details](docs/AGENT_ACCESS.md)); `--toolsets song,audio` gives a whole-song agent a smaller tool list. To configure a client by hand, launch `python -m composerbench.mcp.server` with these environment variables:

```sh
COMPOSERBENCH_ADAPTER=magda_serve \
COMPOSERBENCH_CLI=/absolute/path/to/magda_cli \
COMPOSERBENCH_PROJECT=/absolute/path/to/project.mgd \
python -m composerbench.mcp.server
```

Without `COMPOSERBENCH_PROJECT`, agents create or open projects with `music_project_new` / `music_project_open`; `music_status` reports the active engine. For whole songs, agents use `music_song_check`, `music_song_build` (a background job polled with `music_job_status`) and `music_mixdown`, with `music_device_catalog`, `music_presets`, `music_score_check` and `music_section_report` alongside. Start with `music_project_inspect` and `music_capabilities`. Inspect live instruments and parameters before edits. Use `music_batch_edit` for deliberate changes or `music_revision_run` with explicit objectives for evaluated candidates. The MCP tool names retain the `music_` prefix.

## Documentation

- [Agentic production: scores, song specs, stems, mixing and mastering](docs/AGENTIC_PRODUCTION.md)
- [Agent access: MCP setup, bulk note writing, hosted plugins, parameter labels](docs/AGENT_ACCESS.md)
- [Editing, snapshots and revision workflow](docs/P0_AGENT_WORKFLOW.md)
- [Audio diagnostics, auditioning and resampling](docs/SONIC_WORKFLOW.md)
- [Visual audio review](docs/VISUAL_AUDIO_REVIEW.md)
- [Building MAGDA for ComposerBench](docs/BUILDING_MAGDA.md) — commit, engine patch, Linux and macOS steps
- [MAGDA capability map](docs/MAGDA_CAPABILITY_MAP.md) — historical observations of upstream MAGDA
- [Rename and compatibility](docs/MIGRATION.md)
- [Original design brief](docs/archive/COMPOSERBENCH_MASTER_BUILD_PROMPT.md) — archived; future ambitions, not a feature checklist

Tests requiring MAGDA skip when its local binary is unavailable. Offline tests run without the DAW; the song-builder tests drive a protocol emulator (`tests/_serve_emulator.py`) through complete builds. Song examples beyond the quickstart and sonic workflow are experimental recipes and may require locally installed sample libraries or devices.

## License and dependencies

This initial source publication preserves the project's existing `Proprietary-until-license-audit` status; it does not grant an open-source license. See [LICENSING.md](LICENSING.md). MAGDA and other third-party software remain separately licensed and are not bundled here.
