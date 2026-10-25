# Agentic production: score → song spec → MAGDA renders → mix spec → master

This layer turns ComposerBench from "drive a DAW one command at a time" into a
pipeline an agent can run end to end and iterate on. Everything is
declarative JSON, so an agent changes a value and re-runs a stage instead of
rewriting code. Engine work happens once per build; mixing iterations take
seconds and never touch the engine.

| Stage | Module | CLI | MCP tool |
| --- | --- | --- | --- |
| Write notes | `composerbench.composition.patterns`, `.score` | | |
| Check the score | `composerbench.analysis.harmony` | `composerbench score check` | `music_score_check` |
| Look up devices | `composerbench.devices.catalog` | `composerbench devices [NAME]` | `music_device_catalog` |
| Pick sounds | `composerbench.devices.presets` | `composerbench presets [NAME]` | `music_presets` |
| Preflight a song | `composerbench.song.build.check_song` | `composerbench song check` | `music_song_check` |
| Build + render | `composerbench.song.build.build_song` | `composerbench song build` | `music_song_build` (background job) + `music_job_status` |
| Mix + master | `composerbench.mixing.mixdown.run_mix` | `composerbench mix` | `music_mixdown` |
| Measure | `composerbench.analysis.sections`, `composerbench.mixing.loudness` | `composerbench report` | `music_section_report` |

`composerbench song example` prints a complete 8-bar song (spec + score) that
uses only built-in devices; `composerbench mix example` prints a starter mix spec.

## 1. Writing notes

Notes are plain dicts, `{"start", "duration", "pitch", "velocity"}`, with
times in timeline-absolute beats. Helpers in `composerbench.composition.patterns`:

| Helper | What it does |
| --- | --- |
| `hits(pattern, start, pitch)` | drum string → notes (one character per 16th) |
| `parse_chord("C#m7")`, `chord_pitch_classes`, `chord_root` | chord symbols incl. 7ths, sus, add9, slash chords |
| `voicing(sym, low, high, size, previous)`, `progression([...])` | voicings in a register, voice-led |
| `arpeggio(pitches, order, start, step)` | index patterns over chord tones (wraps across octaves) |
| `legato(notes, slide_flags)` | overlap flagged notes into the next (Legato + Glide synths slide), retrigger the rest |
| `ramp(notes, v0, v1)` | velocity ramps: with a `Vel Filter` patch this is a filter sweep |
| `trim(notes, windows)` | silence windows (the dead beat before a drop) |
| `stutter`, `repeat`, `transpose`, `shift`, `humanize` | |

Drum strings: `x` hit, `X` accent, `o` ghost, `.`/`-` rest, `r` two 32nds,
`R` three 16th triplets, `3` six hits across the next four steps; `|` and
spaces are ignored. `hits("x...x...x...x...", 0, 36)` is four on the floor.

`Score` holds parts, sections (beats; `Score.from_bars(bpm, [("intro", 4), ...])`)
and a chord track (`chords_from_bars(["Am", ["F", "G"], None])`). `to_plan()`
converts the sections to the existing `CompositionPlan`.

`music_score_check` / `check_score()` report avoid notes against the chord
track (a non-chord tone a half step above a chord tone, on strong beats or in
notes of ½ beat or more; tensions a half step below, like major 7ths and
9ths, pass). They also report notes outside a part's range, overlaps in mono
parts and notes per bar per section.

## 2. Devices and presets

The catalog (`composerbench/devices/catalog.json`) lists every MAGDA built-in
instrument and effect plus Surge XT: parameter names, ranges, units, defaults
and labels for discrete parameters (`Osc 1 Wave` = Sine/Saw/Square/Triangle,
`Division` 0..13 = 1/32 … 1/1, EQ band types, reverb engines, …). It was
generated from a live probe plus magda-core's sources; entries say whether
they were verified live or read from source. After rebuilding MAGDA, run
`composerbench devices refresh` to re-probe the engine; it writes
`~/.musicbench/devices.json`, which then takes precedence.

Presets are whole tracks: instrument + settings + FX chain + fader + stem.
The values come from Glitter Rush, whose 15 tracks read back from the engine
with zero mismatches. The available presets are `kick_punch`, `kick_boom`,
`snare_tight`, `clap_wide`, `hat_closed`, `hat_open`, `crash_noise`,
`impact_boom`, `zap_crushed`, `bass_808`, `sub_sine`, `supersaw_surge` (needs
the Surge XT VST3), `supersaw_poly`, `pad_sweep`, `pluck`, `bell`, `bell_fm`,
`lead_glide`, `lead_fm_crushed`, `chop_square`, `riser`, `noise_swell`,
`static_crush` and `beep`.

## 3. Song specs

```json
{
  "title": "demo",
  "score": "score.json",
  "tracks": [
    {"name": "kick", "preset": "kick_punch"},
    {"name": "bass", "preset": "bass_808", "params": {"Glide": 70}, "fx_params": {"0": {"Drive": 6}}},
    {"name": "pad", "instrument": {"device": "magda_polysynth", "params": {"Osc 1 Wave": "Saw", "Cutoff": 1200}},
     "fx": [{"device": "magda_reverb", "params": {"Engine": "Hall", "Mix": 0.3}}],
     "volume": 0.25, "stem": "synths"},
    {"name": "lead", "preset": "lead_glide", "part": "hook", "transpose": 12, "level": 1.0, "pan": 0.1}
  ],
  "render": {"mix": true, "stems": true, "bit_depth": 24, "tail_beats": 4}
}
```

* A track plays its score part (same name, or `"part"`) or inline `"notes"`.
* `params` merges into the instrument; `fx` replaces the preset chain;
  `fx_params` merges into chain entries by index.
* Parameter values are numbers, labels (`"Legato"`, `"1/8."`, `"Hall"`) or
  `{"label": ..., "value": ...}` (label first, number as fallback). Numbers
  outside a parameter's range are clamped and reported.
* `bpm` and `length_beats` default to the score's.
* Faders: `volume` (linear 0..2) wins; otherwise `level` sets it - the part's
  loudness in LU relative to the kick (0 = kick, -1.5 = 808 or supersaws,
  -10 = hats). Every preset has a default level and a measured loudness at a
  unity fader (`devices/preset_levels.json`), so a spec that names only presets
  starts close to balanced. `reference_lufs` (default -23.5) is where a level-0
  part lands in the engine render, leaving headroom for the master stage.
  Re-measure presets for a new engine build with `composerbench presets calibrate`.
* `pan` (-1..1) is set in the engine (protocol 3).

`check_song` resolves presets, checks every device and parameter against the
catalog (typos come back with suggestions), checks notes against the clip,
checks stems, and checks free disk space against the renders it will write.
`build_song` refuses a spec that fails preflight unless `force` is set.

A build writes the following files to `out_dir` (default `~/Music/ComposerBench/<title>`):

| File | Contents |
| --- | --- |
| `mix.wav`, `stems/<stem>.wav` | 24-bit renders; a protocol-3 engine renders each stem from its own tracks, older engines with the other faders at 0 (same audio, ~2.5x slower) |
| `score.json` | the notes exactly as played, sections, chords, bpm (the mix stage reads it) |
| `session.json` | per-parameter results (`how`: label / value / clamped / value-fallback), read-back mismatches, note counts, render stats (peak, RMS, LUFS, true peak), timings, `problems` |
| `spec.resolved.json` | presets expanded into concrete devices |
| `project/` | the MAGDA project |

Notes go to the engine in one bulk request per clip and parameters in one
request per device, with one save per build. A failed track is recorded
and the rest of the song still builds. The build also checks free disk space before
every render and stops cleanly instead of writing a truncated file.

## 4. Mix specs

MAGDA's protocol 2 has no automation, sends, sidechain or master bus. The mix
stage does those jobs on the stems, driven by a spec:

```json
{
  "score": "score.json", "stems_dir": "stems",
  "stems": {
    "drums": {},
    "bass": {"gain_db": -1, "mono_below_hz": 150},
    "synths": {"eq": [{"type": "highpass", "freq": 150}],
               "automation": {"lowpass_hz": [["intro", 400], ["intro.end", 18000]]}},
    "fx": {"automation": {"gain_db": [[0, -6], ["verse1", 0]]}}
  },
  "sidechain": [{"trigger": {"part": "kick"}, "targets": ["bass", "synths"], "depth_db": 3,
                 "section_depth_db": {"drop1": 8, "drop2": 8}, "release_ms": 140}],
  "cuts": [{"start": "pre1.end-1", "end": "pre1.end", "keep": ["fx"]}],
  "edits": [{"type": "reverse_swell", "start": "drop1-2", "end": "drop1", "source_start": "drop1", "stems": ["synths"]},
            {"type": "telephone", "start": 0, "end": "intro.end", "stems": ["synths"]},
            {"type": "tape_stop", "start": "outro+8", "length": 2, "silence_after": true}],
  "bus": {"eq": [{"type": "highpass", "freq": 25, "order": 4}], "glue": {"threshold_db": -14, "ratio": 2},
          "mono_below_hz": 120},
  "master": {"target_lufs": -9, "ceiling_dbtp": -1.0, "fade_out": 2},
  "export": {"basename": "master", "mp3": true}
}
```

* Times are beats (when the score or `bpm` gives a tempo) or seconds
  (`"time_unit": "seconds"`). Any time can be a section reference: `"drop1"`,
  `"drop1.end"`, `"pre1.end-1"`, `"chorus2+8"`.
* Sidechain triggers come from a score part (exact note times; optionally
  velocity-scaled), explicit `times`, or onsets detected in a stem.
* Cuts silence every stem except `keep`, with short fades.
* Edits: `tape_stop`, `stutter` (`slice`), `reverse_swell`, `telephone`,
  `bitcrush`, `gain`, `mute`. Each edit applies to listed `stems` before the
  sum, or to the whole mix after the bus.
* Master: optional drive and soft clip, a loudness target reached
  iteratively through a look-ahead limiter on 4× oversampled peaks, and a
  true-peak ceiling enforced on the result. Then fades and trim, and a
  dithered 16/24-bit or float WAV, plus MP3/FLAC when ffmpeg is installed.

The report (`<basename>_report.json`) holds what was applied, glue and
limiter gain reduction, the master's integrated LUFS, true peak, LRA and
short-term loudness per second. It also has a per-section breakdown of the
mix and every stem (LUFS, peak, crest, centroid, width, band balance) and
flags such as "every section is within 1 LU". A spectrogram PNG with section
markers sits next to it.

Loudness follows ITU-R BS.1770-4 / EBU R128. The K-weighting coefficients
match the standard at 48 kHz, and results agree with FFmpeg's `ebur128` to
within 0.05 LU.

## 5. Running it

```sh
composerbench song example --score-out score.json > song.json
composerbench song check song.json
composerbench song build song.json --out ~/Music/ComposerBench/demo     # needs magda_cli
composerbench mix example > ~/Music/ComposerBench/demo/mix.json
composerbench mix ~/Music/ComposerBench/demo/mix.json
composerbench report ~/Music/ComposerBench/demo/master.wav --score ~/Music/ComposerBench/demo/score.json
```

From Python, call `await build_song("song.json")` and `run_mix("mix.json")`.
Over MCP, `music_song_build` returns a job id at once; poll
`music_job_status` until it is `done`, then call `music_mixdown`.

## Balance pass

Preset levels are measured on a short probe phrase; a song's own parts are
denser or sparser, higher or lower. After a build, the balance pass renders
every track alone, measures it per section (its level is the median of the
sections within 6 LU of its loudest), and moves each fader so the part sits at
its level:

```sh
composerbench song balance ~/Music/ComposerBench/demo            # report only
composerbench song balance ~/Music/ComposerBench/demo --apply    # write faders, rebuild stems + mix.wav
composerbench song balance DIR --apply --levels '{"pad": -4}'     # override targets
```

Over MCP: `music_balance` (a background job; about 20 s per track). With
`apply`, the stems and `mix.wav` are rebuilt from the track renders with the
new gains, exactly as a re-render would be, so `music_mixdown` can run at once.
`balance.json` lists every move and a `spec_patch` with the new volumes.

## Checks that stand in for listening

* `check_score` (and `music_score_check`): harmony clashes, ranges, mono
  overlaps, and runaway notes - a note that rings on into a section where its
  part plays nothing else (a slide or legato reaching a far-off note), or any
  note longer than 8 bars.
* Mix reports carry `warnings`: a section louder than one it should build to
  (from the sections' `energy`), a bass-heavy tilt (1-4 kHz more than 10 dB
  under the low end), no top end, a nearly mono mix, or a stem buried more than
  15 dB under the loudest stem where it plays.

## Engine-side mixing (protocol 3)

With the patched engine an agent can also mix inside MAGDA, where the result is
part of the project and every render: `music_mixer_set` (volume in dB, pan,
mute, solo, sends to aux tracks), `music_fx_add` with `track: "master"`,
`position` or `sidechain_from` (key the built-in `sidechain` ducker or a
compressor from the kick), `music_automation_write` (volume, pan, send and
parameter lanes in real units), `music_clip_edit` and `music_stems_render`.
The adapter methods are `set_mixer`, `set_send`, `add_effect`, `set_sidechain`,
`write_automation`, `clip_edit` and `render_stems` on `MagdaServeAdapter`.

## Limits

* No singing voice, and no audio clips in MAGDA, so vocals are out of scope
  for now.
* The mix stage works per stem, after rendering: a filter sweep on the
  `synths` stem moves every track in that stem. For per-track moves use the
  engine-side tools above (song specs do not describe automation or sends yet).
* Surge XT parameters are normalised 0..1, and only the catalogued subset is
  verified.

Tests: `tests/test_composition_helpers.py`, `test_devices_catalog.py`,
`test_mixing.py`, `test_song_builder.py`, `test_mcp_production.py`,
`test_p3_protocol.py` and `test_cli_production.py`. The
builder tests run a whole song against the serve emulator. With
`EMU_CATALOG=1` it uses the catalog's parameters, and with
`EMU_RENDER_AUDIO=1` it renders note bursts so that stems can be checked to
sum to the mix.
