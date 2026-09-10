"""Render a real synth audition, a gain-bias control and a resampling example."""
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from composerbench.adapter.base import RenderRequest
from composerbench.analysis.audition import compare_audio, inspect_audio
from composerbench.analysis.wav import read_wav, write_float_wav
from composerbench.client import ComposerBench
from composerbench.production import resample_audio


async def run(work, output, cli):
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    if (work / 'Sonic_Lab').exists():
        raise ValueError('choose a fresh work folder; existing project is preserved')
    client = ComposerBench.connect_magda(cli, work / 'Sonic_Lab.mgd', serve=True)
    try:
        await client.initialize()
        await client.batch([
            {'type': 'set_tempo', 'bpm': 168},
            {'type': 'create_track', 'name': 'Digital hook'},
            {'type': 'load_device', 'track': 'Digital hook', 'name': 'magda_polysynth'},
            {'type': 'create_clip', 'track': 'Digital hook', 'name': 'four bars', 'start_beats': 0, 'length_beats': 16},
            {'type': 'add_notes', 'track': 'Digital hook', 'clip': 'four bars', 'notes': [
                {'start': start, 'pitch': pitch, 'duration': duration, 'velocity': velocity}
                for start, pitch, duration, velocity in [
                    (0, 68, .4, 100), (.75, 68, .18, 78), (1.5, 71, .4, 96), (2.5, 75, .65, 110),
                    (4, 73, .35, 95), (4.75, 71, .4, 87), (5.5, 68, .6, 108), (7, 66, .5, 85),
                    (8, 68, .4, 100), (8.75, 71, .18, 78), (9.5, 75, .4, 96), (10.5, 78, .65, 110),
                    (12, 75, .35, 95), (12.75, 73, .4, 87), (13.5, 71, .6, 108), (15, 68, .5, 85)]]},
        ])
        device = (await client.devices('Digital hook'))[0]
        device_ref = device.stable_id or device.id
        settings = {'Osc 1 Wave': 1, 'Osc 1 Level': -12, 'Osc 1 Fine': -7,
                    'Osc 2 Wave': 2, 'Osc 2 Level': -19, 'Osc 2 Fine': 7,
                    'Cutoff': 1400, 'Resonance': .2, 'Filter Env': 1.2,
                    'Filter Attack': 2, 'Filter Decay': 130, 'Filter Sustain': .15,
                    'Amp Attack': 3, 'Amp Decay': 150, 'Amp Sustain': .5,
                    'Amp Release': 100, 'Output': -7}
        parameters = await client.parameters('Digital hook', device_ref)
        edits = []
        for name, value in settings.items():
            parameter = next((key for key, metadata in parameters.items() if metadata.get('name') == name), None)
            if parameter is None:
                raise ValueError(f'live synth does not expose {name}')
            edits.append({'type': 'set_parameter', 'track': 'Digital hook', 'device': device_ref,
                          'parameter': str(parameter), 'value': value})
        await client.batch(edits)
        baseline = await client.render(RenderRequest(work / 'hook_dark.wav', length_beats=16))
        cutoff = next(key for key, metadata in parameters.items() if metadata.get('name') == 'Cutoff')
        await client.batch([{'type': 'set_parameter', 'track': 'Digital hook', 'device': device_ref,
                             'parameter': str(cutoff), 'value': 6500}])
        candidate = await client.render(RenderRequest(work / 'hook_bright.wav', length_beats=16))
    finally:
        await client.close()
    comparison = compare_audio(baseline, candidate, work, duration_seconds=5.5)
    samples, rate = read_wav(candidate)
    boosted = write_float_wav(work / 'gain_only.wav', samples * 10**(4 / 20), rate)
    control = compare_audio(candidate, boosted, work, duration_seconds=5.5)
    beat = 60 / 168
    slices = [
        {'start_seconds': 0, 'end_seconds': 4 * beat},
        {'start_seconds': 4 * beat, 'end_seconds': 6 * beat, 'reverse': True},
        {'start_seconds': 1.5 * beat, 'end_seconds': 2 * beat, 'repeat': 4, 'semitones': 12},
        {'start_seconds': 8 * beat, 'end_seconds': 12 * beat, 'semitones': 7},
        {'start_seconds': 12 * beat, 'end_seconds': 16 * beat, 'reverse': True},
    ]
    chopped = resample_audio(candidate, work, slices,
                             gain_points=[{'seconds': 0, 'db': -6}, {'seconds': .02, 'db': 0},
                                          {'seconds': .3, 'db': 0}, {'seconds': .36, 'db': -12},
                                          {'seconds': .45, 'db': -3}, {'seconds': .7, 'db': 0}], fade_ms=4)
    for name, source in [('01_filter_matched_A_then_B.wav', comparison['files']['A_then_B']),
                         ('02_gain_only_matched_A_then_B.wav', control['files']['A_then_B']),
                         ('03_original_hook.wav', str(candidate)),
                         ('04_chopped_pitched_reversed.wav', chopped['file'])]:
        target = output / name
        if target.exists():
            raise FileExistsError(target)
        shutil.copyfile(source, target)
        inspect_audio(target)
    report = {'filter_comparison': comparison, 'gain_only_control': control, 'resample': chopped}
    (output / 'sonic_example_evidence.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({'examples': str(output), 'filter_pair_mismatch_lu': comparison['loudness_mismatch_lu'],
                      'gain_only_detected': control['gain_only_change'],
                      'resampled_seconds': chopped['audio']['duration_seconds']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cli', type=Path, default=Path.home() / 'magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli')
    arguments = parser.parse_args()
    asyncio.run(run(arguments.work.resolve(), arguments.output.resolve(), arguments.cli))
