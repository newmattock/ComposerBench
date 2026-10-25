"""Evidence and level-matched listening, deliberately without a quality score."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import numpy as np

from composerbench.analysis.wav import read_wav, write_float_wav


def db(value):
    return max(-120.0, 20 * math.log10(max(float(value), 1e-6)))


def excerpt(path, start_seconds=0.0, duration_seconds=None):
    path = Path(path).resolve()
    if not math.isfinite(start_seconds) or start_seconds < 0:
        raise ValueError('start_seconds must be finite and nonnegative')
    if duration_seconds is not None and (not math.isfinite(duration_seconds) or not 0 < duration_seconds <= 180):
        raise ValueError('duration_seconds must be in (0, 180]')
    if path.stat().st_size > 256 * 1024**2:
        raise ValueError('audio exceeds 256 MiB; export a shorter excerpt first')
    samples, rate = read_wav(path)
    if samples.shape[1] not in (1, 2):
        raise ValueError('audition currently supports mono/stereo only')
    first = round(start_seconds * rate)
    last = len(samples) if duration_seconds is None else first + round(duration_seconds * rate)
    if first >= len(samples) or last > len(samples) or last <= first:
        raise ValueError('requested excerpt is empty or outside the source')
    if (last - first) / rate > 180:
        raise ValueError('choose an audition excerpt of at most 180 seconds')
    return samples[first:last], rate


def loudness(samples, rate):
    executable = shutil.which('ffmpeg')
    if not executable:
        return {'status': 'unavailable', 'reason': 'FFmpeg is not installed',
                'integrated_lufs': None, 'true_peak_dbtp': None, 'lra_lu': None}
    with tempfile.TemporaryDirectory(prefix='composerbench-meter-') as directory:
        source = write_float_wav(Path(directory) / 'input.wav', samples, rate)
        result = subprocess.run([executable, '-nostdin', '-hide_banner', '-i', str(source),
                                 '-af', 'loudnorm=print_format=json', '-f', 'null', '-'],
                                capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(f'FFmpeg meter failed: {result.stderr[-1000:]}')
    values = None
    for offset, character in enumerate(result.stderr):
        if character != '{':
            continue
        try:
            decoded, _ = json.JSONDecoder().raw_decode(result.stderr[offset:])
            if 'input_i' in decoded:
                values = decoded
                break
        except (ValueError, TypeError):
            continue
    if values is None:
        raise RuntimeError('FFmpeg returned no loudness measurement')
    def finite(name):
        number = float(values[name])
        return number if math.isfinite(number) else None
    integrated = finite('input_i') if len(samples) / rate >= 0.4 else None
    if np.max(np.abs(samples)) < 1e-9:
        integrated = None
    return {'status': 'measured' if integrated is not None else 'insufficient_signal',
            'method': 'FFmpeg loudnorm input measurement; no processing applied',
            'integrated_lufs': integrated, 'true_peak_dbtp': finite('input_tp'),
            'lra_lu': finite('input_lra') if len(samples) / rate >= 3 else None}


def describe(samples, rate, origin_seconds=0.0):
    rms = float(np.sqrt(np.mean(samples**2)))
    peak = float(np.max(np.abs(samples)))
    windows = []
    for first in range(0, len(samples), rate):
        part = samples[first:first + rate]
        part_rms = float(np.sqrt(np.mean(part**2)))
        part_peak = float(np.max(np.abs(part)))
        windows.append({'start_seconds': round(origin_seconds + first / rate, 4),
                        'end_seconds': round(origin_seconds + (first + len(part)) / rate, 4),
                        'rms_dbfs': db(part_rms), 'sample_peak_dbfs': db(part_peak),
                        'crest_db': db(part_peak) - db(part_rms)})
    size = min(4096, len(samples))
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    spectrum = np.zeros(len(frequencies))
    mono_spectrum = np.zeros(len(frequencies))
    for first in range(0, len(samples), max(1, size // 2)):
        part = samples[first:first + size]
        if len(part) < size:
            part = np.pad(part, ((0, size - len(part)), (0, 0)))
        transformed = np.fft.rfft(part * np.hanning(size)[:, None], axis=0)
        spectrum += np.mean(np.abs(transformed)**2, axis=1)
        mono_spectrum += np.abs(transformed.mean(axis=1))**2
    total = float(spectrum.sum())
    bands = {'sub_20_120': (20, 120), 'body_120_500': (120, 500),
             'mid_500_2000': (500, 2000), 'presence_2000_6000': (2000, 6000),
             'air_6000_nyquist': (6000, rate / 2 + 1)}
    fractions = {name: float(spectrum[(frequencies >= lower) & (frequencies < upper)].sum()) / max(total, 1e-30)
                 for name, (lower, upper) in bands.items()}
    mono_rms = float(np.sqrt(np.mean(samples.mean(axis=1)**2)))
    low = (frequencies >= 20) & (frequencies < 120)
    low_power = float(spectrum[low].sum())
    centered = samples - samples.mean(axis=0)
    correlation = None
    if samples.shape[1] == 2 and np.prod(np.std(centered, axis=0)) > 1e-15:
        correlation = float(np.corrcoef(centered.T)[0, 1])
    low_loss = 0.5 * db(float(mono_spectrum[low].sum()) / low_power) if low_power > max(total * 1e-6, 1e-20) else None
    mono_loss = db(mono_rms / rms) if rms > 1e-12 else None
    findings = []
    if peak >= 1:
        findings.append({'kind': 'full_scale_samples', 'evidence': float(np.mean(np.abs(samples) >= 1)),
                         'action': 'Check the source for clipping before limiting or normalization.'})
    if mono_loss is not None and mono_loss < -6:
        findings.append({'kind': 'mono_cancellation', 'evidence_db': mono_loss,
                         'action': 'Audition mono; inspect polarity, stereo delays and unison width.'})
    if low_loss is not None and low_loss < -3:
        findings.append({'kind': 'low_end_cancellation', 'evidence_db': low_loss,
                         'action': 'Compare kick/bass in mono before changing EQ or compression.'})
    for window in windows:
        if window['rms_dbfs'] < -65:
            findings.append({'kind': 'quiet_window', 'start_seconds': window['start_seconds'],
                             'end_seconds': window['end_seconds'], 'action': 'Check whether this gap or tail is intentional.'})
    return {'duration_seconds': len(samples) / rate, 'sample_rate': rate,
            'channels': samples.shape[1], 'sample_peak_dbfs': db(peak), 'rms_dbfs': db(rms),
            'crest_db': db(peak) - db(rms), 'dc_offset_by_channel': samples.mean(axis=0).tolist(),
            'band_power_fraction': fractions, 'stereo_correlation': correlation,
            'mono_loss_db': mono_loss, 'low_band_mono_loss_db': low_loss,
            'windows': windows, 'findings': findings,
            'interpretation': 'Technical evidence only; spectral balance and distortion are not quality scores.'}


def inspect_audio(path, start_seconds=0.0, duration_seconds=None):
    samples, rate = excerpt(path, start_seconds, duration_seconds)
    return {'source': str(Path(path).resolve()), 'start_seconds': start_seconds,
            **describe(samples, rate, start_seconds), 'loudness': loudness(samples, rate)}


def compare_audio(baseline, candidate, output_dir, start_seconds=0.0,
                  duration_seconds=None, candidate_start_seconds=None, ceiling_dbtp=-1.0,
                  target_lufs=-18.0):
    """Export A/B at equal integrated loudness using constant gains, never a limiter."""
    if not math.isfinite(ceiling_dbtp) or not -24 <= ceiling_dbtp <= -0.5:
        raise ValueError('ceiling_dbtp must be between -24 and -0.5')
    if not math.isfinite(target_lufs) or not -36 <= target_lufs <= -9:
        raise ValueError('target_lufs must be between -36 and -9')
    before, rate = excerpt(baseline, start_seconds, duration_seconds)
    after, other_rate = excerpt(candidate, start_seconds if candidate_start_seconds is None else candidate_start_seconds, duration_seconds)
    if rate != other_rate or before.shape != after.shape:
        raise ValueError('A/B requires equal sample rate, channel count and excerpt length; select explicit corresponding ranges')
    measurements = [loudness(part, rate) for part in (before, after)]
    if any(item['status'] != 'measured' or item['true_peak_dbtp'] is None for item in measurements):
        raise ValueError('A/B requires FFmpeg and measurable non-silent excerpts of at least 400 ms')
    target = min(target_lufs, *(item['integrated_lufs'] + ceiling_dbtp - item['true_peak_dbtp'] - 0.15 for item in measurements))
    gains = [target - item['integrated_lufs'] for item in measurements]
    matched = [part * 10**(gain / 20) for part, gain in zip((before, after), gains)]
    verified = [loudness(part, rate) for part in matched]
    if any(item['integrated_lufs'] is None or item['true_peak_dbtp'] is None for item in verified):
        raise ValueError('Matched excerpts are below the measurement gate; choose a more audible range')
    mismatch = abs(verified[0]['integrated_lufs'] - verified[1]['integrated_lufs'])
    if mismatch > 0.3 or any(item['true_peak_dbtp'] > ceiling_dbtp for item in verified):
        raise ValueError('Constant-gain loudness matching failed verification; choose more stable excerpts')
    destination = Path(output_dir).resolve() / ('audition-' + uuid.uuid4().hex[:12])
    destination.mkdir(parents=True)
    paths = []
    for label, part in zip(('A', 'B'), matched):
        paths.append(str(write_float_wav(destination / f'{label}.wav', part, rate)))
    for label, part in zip(('A_mono', 'B_mono'), matched):
        write_float_wav(destination / f'{label}.wav', part.mean(axis=1, keepdims=True), rate)
    gap = np.zeros((round(0.3 * rate), before.shape[1]))
    sequence = write_float_wav(destination / 'A_then_B.wav', np.concatenate([matched[0], gap, matched[1]]), rate)
    normalized = [part / max(float(np.sqrt(np.mean(part**2))), 1e-12) for part in (before, after)]
    residual = float(np.sqrt(np.mean((normalized[0] - normalized[1])**2)))
    report = {'status': 'ready_for_listening', 'quality_decision': 'not_automatically_judged',
              'baseline': str(Path(baseline).resolve()), 'candidate': str(Path(candidate).resolve()),
              'source_sha256': [hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in (baseline, candidate)],
              'start_seconds': start_seconds,
              'candidate_start_seconds': start_seconds if candidate_start_seconds is None else candidate_start_seconds,
              'duration_seconds': len(before) / rate, 'target_lufs': target,
              'requested_target_lufs': target_lufs,
              'applied_gain_db': gains, 'verified_loudness': verified,
              'loudness_mismatch_lu': mismatch, 'gain_normalized_residual_rms': residual,
              'gain_only_change': residual < 1e-4,
              'alignment': 'Caller-selected corresponding excerpts; no automatic phase or latency alignment.',
              'files': {'A': paths[0], 'B': paths[1], 'A_then_B': str(sequence),
                        'A_mono': str(destination / 'A_mono.wav'), 'B_mono': str(destination / 'B_mono.wav')},
              'before': describe(before, rate, start_seconds),
              'after': describe(after, rate, start_seconds if candidate_start_seconds is None else candidate_start_seconds),
              'listening_questions': ['Is the hook clearer at equal loudness?',
                                     'Does the bass still work in mono?',
                                     'Did transients, grit or emotional contrast improve?',
                                     'Is the change useful in context, not just in solo?']}
    (destination / 'comparison.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    return report
