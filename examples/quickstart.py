"""Create and inspect a small arrangement without a DAW or external samples."""
import argparse
import asyncio
import json
from pathlib import Path

from composerbench.client import ComposerBench


async def run(project):
    client = ComposerBench.connect_fake(project)
    try:
        await client.batch([
            {'type': 'create_track', 'name': 'lead'},
            {'type': 'create_clip', 'track': 'lead', 'name': 'phrase',
             'start_beats': 0, 'length_beats': 4},
            {'type': 'add_notes', 'track': 'lead', 'clip': 'phrase',
             'notes': [{'start': i, 'duration': 0.5, 'pitch': p, 'velocity': 90}
                       for i, p in enumerate([60, 64, 67, 72])]},
        ])
        print(json.dumps({"tracks": [t.name for t in await client.tracks()],
                          "notes": [n.model_dump() for n in await client.notes("lead")]}, indent=2))
    finally:
        await client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True,
                        help='Fresh directory for the example project')
    args = parser.parse_args()
    if args.project.exists():
        parser.error('Choose a fresh project directory to preserve existing work.')
    asyncio.run(run(args.project))
