# Sonic decisions, auditioning and offline resampling

This extends the existing transactional P0 workflow. It does not replace its
snapshots, stable handles, explicit objectives or rejection/rollback behavior.
Technical measurements are evidence, not ratings of a song.

## What is available

- `music_audio_inspect`: an excerpt's integrated LUFS, true peak, LRA where
  meaningful, sample peak/RMS/crest, frequency-band power, per-channel DC,
  stereo correlation, mono/low-band cancellation and one-second timed windows.
- `music_audio_compare`: constant-gain, loudness-matched A/B WAVs, a sequential
  A-then-B WAV, mono versions, source hashes and a persisted comparison report.
  A is baseline and B is candidate; this is **not a blinded or randomized trial**.
- `music_audio_resample`: reorder, reverse, repeat and varispeed slices; short
  anti-click edge fades; sample-accurate output-time gain envelopes.
- `music_render`: defaults to 24-bit PCM; accepts `bit_depth=16` when needed.
  Real MAGDA adapters now reject `track_ids` rather than silently returning the
  entire mix and mislabeling it as an isolated stem.
- `music_revision_run`: optional `audition_start_seconds` and
  `audition_duration_seconds` attach a matched pair to revision evidence/history,
  including rejected candidates. Default audition duration is eight seconds;
  choose an active part of the arrangement rather than a silent intro.

All three audio tools are also async Python facade methods: `audio_inspect`,
`audio_compare`, `audio_resample`. They do not require a running DAW. Long scans
run off the event loop; cancellation can leave an offline worker finishing its
bounded operation, but it does not edit DAW state or the source file.

## A useful loop

1. Choose an active 4–8-bar region containing the problem. State one hypothesis,
   such as “the lead's filter masks consonants” or “stereo bass cancels in mono.”
2. Render the baseline. Make one deliberate change through `batch` or `revise`.
3. Compare the same musical range at equal LUFS, then in mono. Inspect timed
   findings to decide where to listen, not what the listener must prefer.
4. Keep a subjective judgment separate from objective regression constraints.
   Brighter, louder, wider and more compressed are not synonyms for better.
5. Check the chosen edit again in the full arrangement and at a quiet monitoring
   level. A solo improvement may worsen the mix. Record what was actually heard.

```python
from composerbench.client import ComposerBench

mb = ComposerBench.connect_fake('/tmp/offline-auditions')
evidence = await mb.audio_inspect(
    path='/absolute/mix.wav', start_seconds=20, duration_seconds=8)
pair = await mb.audio_compare(
    baseline='/absolute/before.wav', candidate='/absolute/after.wav',
    start_seconds=20, duration_seconds=8, target_lufs=-18)
print(pair['files']['A_then_B'])
print(pair['files']['A_mono'], pair['files']['B_mono'])
```

`connect_fake` above is only a host for offline file methods. Its synthesized
test tone must never be used to evaluate musical production.

### Reference tracks

An authorized reference WAV can be the baseline. Supply
`candidate_start_seconds` when its corresponding section begins elsewhere.
Both excerpts must have the same length, sample rate and channel count.
The comparison never silently trims, stretches, aligns or resamples a reference.
The gain-normalized residual is only informative for sample-aligned variants;
unrelated recordings or latency changes will not null.

### Revision evidence

```python
record = await mb.revise(
    goal='Audition a filter change without mistaking gain for timbre',
    actions=[{'type': 'set_parameter', 'track': 'lead', 'device': 'mb:...',
              'parameter': 'Cutoff', 'value': 4200}],
    audition_start_seconds=20, audition_duration_seconds=8)
```

Use handles and parameter names returned by live inspection. With no explicit
objective or enabled critic the candidate is still rejected/restored. Its
matched audio remains available for listening; exporting an audition does not
approve the edit. A failed optional audition is marked unavailable and does not
masquerade as a successful listening comparison. Existing heuristic critic
acceptance remains distinct from a listener's judgment.

## Resampling and envelope recipe

```python
sample = await mb.audio_resample(
    source='/absolute/hook.wav',
    slices=[
        {'start_seconds': 0, 'end_seconds': 0.25, 'repeat': 4},
        {'start_seconds': 0.5, 'end_seconds': 1.0,
         'reverse': True, 'semitones': 12},
    ],
    gain_points=[{'seconds': 0, 'db': -18},
                 {'seconds': 0.02, 'db': -18},
                 {'seconds': 0.15, 'db': 0}],
    fade_ms=3)
```

Pitch uses FFmpeg `asetrate` followed by band-limited `aresample`, not naive
linear sample skipping. An octave up halves duration and raises formants too.
This deliberately does not claim independent stretching, formant preservation,
automatic pitch correction, scale snapping or a vocal model. Zero semitones
bypasses the resampler. Repeated slices retain their original grid duration;
edge fades shorten neither the slice nor the sequence. Envelope points are in
seconds on the **output** timeline, interpolated in dB, with endpoint values
held outside the listed range. Over-full-scale output is rejected, not clipped
or automatically limited. This is scheduled ducking, not detected sidechain
compression. Generated WAVs are not automatically inserted into the DAW.

## Measurement and safety boundaries

- LUFS/true peak use FFmpeg `loudnorm` **input measurements**. The filter's
  processed output is discarded. Stereo spectral analysis sums channel power,
  so anti-phase bass does not disappear from the spectral measurement.
- Auditions request -18 LUFS by default. Both files receive only constant gain.
  If either needs more peak headroom, the common target is lowered instead of
  compressing/limiting one file. A 0.15 dB margin is reserved before export.
  Both results are re-measured: mismatch must be <=0.3 LU and true peak must
  remain under the requested ceiling. Match/export is rejected otherwise.
- Float WAV preserves intermediate precision. Original input files are never
  overwritten. Every operation writes a uniquely named output directory and
  JSON evidence; comparison reports include hashes of both full source files.
- No FFmpeg: pure NumPy diagnostics still work, loudness fields are null with
  an explicit unavailable status, and matched A/B or pitched recipes refuse to
  pretend they succeeded. Silent/sub-400ms material is not valid LUFS matching
  evidence. Very short excerpts have weak LRA meaning; under three seconds LRA
  is reported null. LUFS gating can make very different/dynamic excerpts harder
  to match with a single gain; choose comparable sections instead.
- Limits: mono/stereo WAV, <=256 MiB input, <=180-second selected excerpts and
  resampled outputs, <=128 slices, <=32 repeats/slice, <=4096 envelope points.
  WAV decoding currently loads the bounded source into memory. Long files must
  be exported as shorter WAV excerpts first. Sample rates are read from files.
- No output adds lyrics, detects pitch, measures intelligibility, diagnoses
  masking between isolated sources, or establishes professional sound quality.

FFmpeg's authoritative descriptions:
[loudnorm](https://ffmpeg.org/ffmpeg-filters.html#loudnorm),
[asetrate](https://ffmpeg.org/ffmpeg-filters.html#asetrate),
[aresample](https://ffmpeg.org/ffmpeg-filters.html#aresample).

## Reproduce and test

```sh
.venv/bin/python -m pytest -q tests/test_sonic_workflow.py
.venv/bin/python -m pytest -q
.venv/bin/python examples/sonic_workflow.py \
  --work /absolute/fresh-work --output /absolute/fresh-outputs
```

The example renders a real MAGDA synth through ComposerBench's transactional facade,
changes its cutoff, exports a matched A/B, includes a +4 dB gain-only negative
control, and chops/reverses/transposes the rendered hook. It does not download
plugins or use external recordings. `MAGDA` must have the internal polysynth;
pass `--cli` for a nondefault binary location.
