# Local visual audio review

Install the optional renderer with `pip install -e '.[visual]'`. Analysis uses
NumPy, local Matplotlib Agg rendering and optional local FFmpeg loudness meters.
It does not upload audio or invoke any audio model.

```python
report = await client.audio_visual_review(
    path="/absolute/mix.wav",
    output_dir="/absolute/reviews",
    start_seconds=30,
    duration_seconds=8,
    stems={"Kick": "/absolute/kick.wav", "Bass": "/absolute/bass.wav"},
)
```

The same interface is exposed through MCP as `music_audio_visual_review`.
Open `report['files']['html']`, inspect the PNG files with image tools, or use
the JSON evidence to plan a controlled edit. Each call creates a unique folder;
inputs are never modified. Mono/stereo WAVs only, maximum 180 seconds per
excerpt, 256 MiB per input and eight optional stems. Inputs must share sample
rate and selected duration; stems must already be aligned to the mix timeline.
The tool does not infer latency or claim that stems sum to a mastered mix.

## What the first version supplies

- Two Hann-window spectrograms: 2048 samples for timing and 8192 for frequency
  detail (short excerpts use smaller windows). One-sided power spectral density
  is averaged across channels, so out-of-phase stereo content cannot disappear
  through accidental mono summation. Color scale is always -100 to -20 dBFS/Hz.
- Full-coverage sample-peak and RMS envelopes; isolated peaks are retained even
  for long excerpts. These are not true-peak measurements or onset detection.
- Bandwise stereo correlation and mono-sum loss, including explicit inactive
  bands. JSON retains cancellation values below the plot's -30 dB display floor.
- Pairwise stem shared-power ratios with source timestamps. This measures the
  intersection of PSD cells relative to the larger source power, gated below
  -60 dBFS RMS. Relative level matters. Overlap is not proof of masking.
- Source hashes, selected range, mix loudness when FFmpeg is available, and
  existing timed full-scale/quiet-window/cancellation findings.

## Decision workflow

1. Review a whole-song overview (up to 180 seconds), then a short suspect region.
2. Identify a source and state a testable hypothesis, not an aesthetic verdict.
3. Make one edit; render again. Run `audio_compare` for verified constant-gain
   loudness-matched A/B audio, then review its A and B files at start zero with
   this tool. Fixed visual scales alone do not imply loudness matching.
4. Keep a change only with supporting evidence and, where needed, human audition.

Current limits: spectral time bins are averaged to at most 800 columns, which
can smear transients; use short excerpts to zoom. This is not hearing, a
psychoacoustic masking model, a decay estimator, automatic reference matching,
or a professional-quality score. Interaction timestamps identify analysis-cell
centers rather than precisely segmented musical events. Planned improvements
include aligned difference plots, onset-conditioned kick/bass decay analysis,
and an overview-to-detail navigator.
