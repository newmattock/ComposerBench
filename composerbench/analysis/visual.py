"""Local visual evidence, not a perceptual quality judge."""
from __future__ import annotations

import hashlib
import html
import json
import threading
import uuid
from pathlib import Path

import numpy as np

from composerbench.analysis.audition import describe, excerpt, loudness

_PLOT_LOCK = threading.Lock()
BANDS = [(20, 120), (120, 500), (500, 2000), (2000, 6000), (6000, 24000)]


def spectral_map(samples, rate, size, columns=800):
    """One-sided Hann PSD; average channel powers before any mono summation."""
    size = min(size, len(samples))
    window = np.hanning(size)
    hop = max(1, size // 4)
    starts = np.arange(0, max(1, len(samples) - size + 1), hop)
    starts = np.unique(np.append(starts, max(0, len(samples) - size)))
    groups = np.array_split(starts, min(columns, len(starts)))
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    power = np.zeros((len(frequencies), len(groups)))
    times = []
    for column, group in enumerate(groups):
        for first in group:
            transformed = np.fft.rfft(samples[first:first + size] * window[:, None], axis=0)
            density = np.mean(np.abs(transformed)**2, axis=1) / (rate * np.sum(window**2))
            density[1: -1 if size % 2 == 0 else None] *= 2
            power[:, column] += density / len(group)
        times.append((float(group.mean()) + size / 2) / rate)
    return frequencies, np.array(times), power


def time_envelope(samples, rate, columns=800):
    groups = np.array_split(np.arange(len(samples)), min(columns, len(samples)))
    return {
        'seconds': [float(group.mean()) / rate for group in groups],
        'peak_dbfs': [float(20 * np.log10(max(np.max(np.abs(samples[group])), 1e-6))) for group in groups],
        'rms_dbfs': [float(20 * np.log10(max(np.sqrt(np.mean(samples[group]**2)), 1e-6))) for group in groups],
    }


def stereo_bands(samples, rate):
    size = min(8192, len(samples))
    window = np.hanning(size)
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    powers = np.zeros((len(frequencies), 4))
    starts = np.unique(np.append(np.arange(0, len(samples) - size + 1, max(1, size // 2)), len(samples) - size))
    for first in starts:
        transformed = np.fft.rfft(samples[first:first + size] * window[:, None], axis=0)
        left = transformed[:, 0]
        right = transformed[:, -1]
        powers += np.column_stack([np.abs(left)**2, np.abs(right)**2,
                                   np.real(left * np.conj(right)), np.abs((left + right) / 2)**2])
    result = []
    for lower, upper in BANDS:
        left, right, cross, mono = powers[(frequencies >= lower) & (frequencies < upper)].sum(axis=0)
        active = left + right > max(float(powers[:, :2].sum()) * 1e-6, 1e-18)
        result.append({'low_hz': lower, 'high_hz': min(upper, rate / 2),
                       'available': lower < rate / 2,
                       'correlation': float(np.clip(cross / np.sqrt(left * right), -1, 1)) if active and left * right > 0 else None,
                       'mono_loss_db': float(10 * np.log10(max(mono / ((left + right) / 2), 1e-12))) if active else None})
    return result


def overlap_evidence(first, second, frequencies, times, origin):
    audible = (first.sum(axis=0) > 1e-12) & (second.sum(axis=0) > 1e-12)
    delta = frequencies[1] - frequencies[0]
    first_rms = np.sqrt(first.sum(axis=0) * delta)
    second_rms = np.sqrt(second.sum(axis=0) * delta)
    audible &= (first_rms > 10**(-60 / 20)) & (second_rms > 10**(-60 / 20))
    denominator = np.maximum(first.sum(axis=0), second.sum(axis=0))
    score = np.minimum(first, second).sum(axis=0) / np.maximum(denominator, 1e-30)
    score[~audible] = 0
    top = np.argsort(score)[-3:][::-1]
    return {'mean_shared_power_ratio': float(score.mean()),
            'active_both_fraction': float(audible.mean()),
            'regions': [{'seconds': float(origin + times[index]), 'shared_power_ratio': float(score[index])}
                        for index in top if score[index] > .05],
            'interpretation': 'Shared time-frequency power, including relative level; not proof of auditory masking. Solo and test one targeted change.'}


def visual_review(path, output_dir, start_seconds=0.0, duration_seconds=12.0, stems=None):
    """Write fixed-scale PNGs, JSON and a local HTML report for aligned WAVs."""
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError as error:
        raise RuntimeError('Install ComposerBench with the visual extra: pip install -e ".[visual]"') from error
    stems = {} if stems is None else stems
    if not isinstance(stems, dict) or len(stems) > 8 or any(not isinstance(name, str) or not name.strip() for name in stems):
        raise ValueError('stems must map up to eight nonempty labels to aligned WAV paths')
    sources = [('Mix', path), *stems.items()]
    analyzed = []
    shape = None
    sample_rate = None
    for label, source in sources:
        samples, rate = excerpt(source, start_seconds, duration_seconds)
        if rate < 80:
            raise ValueError('visual review requires a sample rate of at least 80 Hz')
        if len(samples) < 32:
            raise ValueError('visual review requires at least 32 samples')
        if shape is not None and (len(samples) != shape or rate != sample_rate):
            raise ValueError('mix and stems require equal sample rate and excerpt duration; no implicit resampling')
        shape, sample_rate = len(samples), rate
        spectra = [spectral_map(samples, rate, size) for size in (2048, 8192)]
        analyzed.append({'label': label, 'source': str(Path(source).resolve()),
                         'sha256': hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                         'metrics': describe(samples, rate, start_seconds),
                         'stereo_bands': stereo_bands(samples, rate),
                         'envelope': time_envelope(samples, rate), 'spectra': spectra})
    destination = Path(output_dir).resolve() / ('visual-' + uuid.uuid4().hex[:12])
    destination.mkdir(parents=True)
    files = []
    with _PLOT_LOCK:
        for index, item in enumerate(analyzed):
            figure = Figure(figsize=(13, 9), constrained_layout=True)
            FigureCanvasAgg(figure)
            axes = figure.subplots(4, 1)
            envelope = item['envelope']
            seconds = np.array(envelope['seconds']) + start_seconds
            axes[0].plot(seconds, envelope['peak_dbfs'], label='Sample peak', linewidth=.8)
            axes[0].plot(seconds, envelope['rms_dbfs'], label='RMS', linewidth=.8)
            axes[0].set_ylim(-80, 6)
            axes[0].set_ylabel('dBFS')
            axes[0].legend(loc='lower right')
            axes[0].set_title(item['label'] + ' — identical scales; no per-source normalization')
            for axis, (frequencies, times, power) in zip(axes[1:3], item['spectra']):
                valid = frequencies >= 20
                time_edges = np.concatenate([[0], (times[:-1] + times[1:]) / 2, [shape / rate]]) + start_seconds
                selected = frequencies[valid]
                spacing = frequencies[1] - frequencies[0]
                frequency_edges = np.append(selected - spacing / 2, selected[-1] + spacing / 2)
                mesh = axis.pcolormesh(time_edges, frequency_edges,
                                       10 * np.log10(np.maximum(power[valid], 1e-12)),
                                       vmin=-100, vmax=-20, shading='flat', cmap='magma')
                axis.set_yscale('log')
                axis.set_ylim(20, rate / 2)
                axis.set_ylabel('Hz')
                axis.set_title(f'Hann PSD — bin spacing {frequencies[1]:.1f} Hz; time-cell averaged')
                figure.colorbar(mesh, ax=axis, label='dBFS/Hz')
            bands = item['stereo_bands']
            axes[3].bar(range(len(bands)), [band['mono_loss_db'] if band['mono_loss_db'] is not None else 0 for band in bands])
            for band_index, band in enumerate(bands):
                label = f"{band['mono_loss_db']:.2f} dB" if band['mono_loss_db'] is not None else 'inactive'
                axes[3].text(band_index, -3, label, ha='center', fontsize=9)
            axes[3].set_xticks(range(len(bands)), [f"{band['low_hz']}–{band['high_hz']:g}" if band['mono_loss_db'] is not None else 'inactive/N/A' for band in bands])
            axes[3].set_ylim(-30, 1)
            axes[3].set_ylabel('Mono loss dB\n(display floor −30)')
            axes[3].set_xlabel('Frequency band, Hz; see JSON for full values')
            for axis in axes[:3]:
                axis.set_xlim(start_seconds, start_seconds + shape / rate)
                axis.set_xlabel('Source time, seconds')
            filename = f'source-{index:02d}.png'
            figure.savefig(destination / filename, dpi=120)
            files.append(filename)
            figure.clear()
    interactions = []
    for first_index in range(1, len(analyzed)):
        for second_index in range(first_index + 1, len(analyzed)):
            first, second = analyzed[first_index], analyzed[second_index]
            frequencies, times, power = first['spectra'][0]
            interactions.append({'sources': [first['label'], second['label']],
                                 **overlap_evidence(power, second['spectra'][0][2], frequencies, times, start_seconds)})
    for item in analyzed:
        del item['spectra']
    report = {'status': 'technical_evidence_only', 'start_seconds': start_seconds,
              'duration_seconds': shape / sample_rate, 'sample_rate': sample_rate,
              'alignment': 'Caller must supply time-zero aligned stems. No automatic alignment or stem-sum assertion.',
              'limits': 'Not hearing, a masking detector, or a quality score. PSD averages channel powers so anti-phase content remains visible. Coarse time cells can smear transients; peak envelopes retain sample peaks. No model or network calls.',
              'mix_loudness': loudness(*excerpt(path, start_seconds, duration_seconds)),
              'sources': analyzed, 'interactions': interactions,
              'files': {'images': [str(destination / filename) for filename in files],
                        'json': str(destination / 'report.json'), 'html': str(destination / 'index.html')}}
    (destination / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    body = ''.join(f'<h2>{html.escape(item["label"])}</h2><img src="{filename}" alt="Analysis plots">' for item, filename in zip(analyzed, files))
    findings = html.escape(json.dumps(interactions, indent=2))
    (destination / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>ComposerBench visual review</title>'
        '<style>body{max-width:1400px;margin:2em auto;font:16px system-ui;background:#eee;color:#222}img{width:100%}pre{white-space:pre-wrap}</style>'
        '<h1>ComposerBench · Visual audio review</h1><p>' + html.escape(report['limits']) + '</p><p>' + html.escape(report['alignment']) + '</p>'
        '<p><a href="report.json">Machine-readable evidence</a></p>' + body + '<h2>Stem interactions: hypotheses, not masking verdicts</h2><pre>' + findings + '</pre>')
    return report
