"""Non-destructive offline sample recipes; not a DAW timeline or vocal tuner."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from composerbench.analysis.audition import describe, excerpt
from composerbench.analysis.wav import write_float_wav


class Slice(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    reverse: bool = False
    repeat: int = Field(default=1, ge=1, le=32)
    semitones: float = Field(default=0, ge=-24, le=24)
    gain_db: float = Field(default=0, ge=-60, le=12)

    @model_validator(mode='after')
    def valid_range(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError('slice end must be after start')
        return self


class GainPoint(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    seconds: float = Field(ge=0)
    db: float = Field(ge=-90, le=12)


def resample_audio(source, output_dir, slices, gain_points=None, fade_ms=3.0):
    """Reorder/repeat/reverse/varispeed slices with anti-click fades and dB envelopes."""
    if not np.isfinite(fade_ms) or not 0 <= fade_ms <= 100:
        raise ValueError('fade_ms must be between 0 and 100')
    recipes = [Slice.model_validate(item) for item in slices]
    if not 1 <= len(recipes) <= 128:
        raise ValueError('provide 1 to 128 slices')
    points = [GainPoint.model_validate(item) for item in (gain_points or [])]
    if len(points) > 4096:
        raise ValueError('at most 4096 gain points are supported')
    if any(right.seconds <= left.seconds for left, right in zip(points, points[1:])):
        raise ValueError('gain points must have strictly increasing times')
    samples, rate = excerpt(source)
    segments = []
    events = []
    total_frames = 0
    for recipe in recipes:
        first, last = round(recipe.start_seconds * rate), round(recipe.end_seconds * rate)
        if last > len(samples) or last <= first:
            raise ValueError('slice is outside source or shorter than one frame')
        part = samples[first:last].copy()
        if recipe.reverse:
            part = part[::-1].copy()
        ratio = 2**(recipe.semitones / 12)
        output_frames = max(1, round(len(part) / ratio))
        if total_frames + output_frames * recipe.repeat > rate * 180:
            raise ValueError('recipe output exceeds 180 seconds')
        if recipe.semitones:
            import shutil
            import subprocess
            import tempfile

            from composerbench.analysis.wav import read_wav
            executable = shutil.which('ffmpeg')
            if not executable:
                raise ValueError('band-limited varispeed requires FFmpeg')
            with tempfile.TemporaryDirectory(prefix='composerbench-pitch-') as directory:
                original = write_float_wav(Path(directory) / 'source.wav', part, rate)
                shifted = Path(directory) / 'shifted.wav'
                command = [executable, '-nostdin', '-hide_banner', '-loglevel', 'error',
                           '-i', str(original), '-af', f'asetrate={round(rate * ratio)},aresample={rate}',
                           '-c:a', 'pcm_f32le', str(shifted)]
                result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
                if result.returncode:
                    raise RuntimeError(result.stderr[-1000:])
                part, _ = read_wav(shifted)
        if total_frames + len(part) * recipe.repeat > rate * 180:
            raise ValueError('recipe output exceeds 180 seconds')
        part *= 10**(recipe.gain_db / 20)
        edge = min(round(fade_ms * rate / 1000), len(part) // 2)
        if edge > 0:
            ramp = np.linspace(0, 1, edge)
            part[:edge] *= ramp[:, None]
            part[-edge:] *= ramp[::-1, None]
        for repeat_index in range(recipe.repeat):
            events.append({'output_start_seconds': total_frames / rate, 'repeat_index': repeat_index,
                           'output_duration_seconds': len(part) / rate, 'slice': recipe.model_dump()})
            segments.append(part)
            total_frames += len(part)
    rendered = np.concatenate(segments)
    if points:
        if points[-1].seconds > len(rendered) / rate:
            raise ValueError('gain point is outside output timeline')
        envelope = np.interp(np.arange(len(rendered)) / rate,
                             [point.seconds for point in points], [point.db for point in points])
        rendered *= 10**(envelope[:, None] / 20)
    if np.max(np.abs(rendered)) >= 1:
        raise ValueError('recipe exceeds full scale; lower slice/envelope gains rather than silently clipping')
    destination = Path(output_dir).resolve() / ('resample-' + uuid.uuid4().hex[:12])
    destination.mkdir(parents=True)
    output = write_float_wav(destination / 'resampled.wav', rendered, rate)
    report = {'file': str(output), 'source': str(Path(source).resolve()),
              'source_sha256': hashlib.sha256(Path(source).read_bytes()).hexdigest(),
              'events': events, 'gain_points': [point.model_dump() for point in points],
              'fade_ms': fade_ms, 'audio': describe(rendered, rate),
              'pitch_mode': 'band-limited varispeed: pitch, duration and formants change together',
              'automation': 'sample-accurate linear interpolation in dB on the output timeline',
              'limitations': ['No independent time stretch or formant preservation',
                              'No pitch detection, scale tuning or vocal generation',
                              'Offline WAV only; no implicit DAW clip insertion']}
    (destination / 'recipe.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    return report
