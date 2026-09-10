"""Project-local stable references over engine-owned numeric handles.

P0 edits preserve track/clip names and device ordering. Descriptors are resolved
against live state on each use; numeric engine IDs are never trusted after reload.
"""
import json
import uuid
from pathlib import Path


class Handles:
    def __init__(self, root: Path):
        self.path = root / 'handles.json'

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def bind(self, kind, name, track=None, ordinal=0):
        descriptor = {'kind': kind, 'name': name, 'track': track, 'ordinal': ordinal}
        entries = self.read()
        for key, value in entries.items():
            if value == descriptor: return key
        key = f'mb:{kind}:{uuid.uuid4().hex}'
        entries[key] = descriptor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(entries, indent=2))
        temp.replace(self.path)
        return key

    def lookup(self, ref, kind):
        entry = self.read().get(ref)
        if entry is None or entry['kind'] != kind:
            raise LookupError(f'unknown stable {kind} handle {ref}')
        return entry
