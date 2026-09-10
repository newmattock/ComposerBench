"""Serialized, cancellation-safe transactions including ComposerBench artistic state."""
from __future__ import annotations
import asyncio
import shutil
import tempfile
from pathlib import Path

# Evidence and snapshot history are append-only, not artistic project state.
EXCLUDED = {'snapshots', 'renders', 'traces', 'evaluations'}


def capture_sidecar(root: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if root.exists():
        for item in root.iterdir():
            if item.name in EXCLUDED: continue
            target = destination / item.name
            if item.is_dir(): shutil.copytree(item, target)
            else: shutil.copy2(item, target)


def restore_sidecar(root: Path, source: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for item in root.iterdir():
        if item.name in EXCLUDED: continue
        if item.is_dir(): shutil.rmtree(item)
        else: item.unlink()
    for item in source.iterdir():
        if item.is_dir(): shutil.copytree(item, root / item.name)
        else: shutil.copy2(item, root / item.name)


def transaction_lock(daw):
    if not hasattr(daw, '_composerbench_transaction_lock'):
        daw._composerbench_transaction_lock = asyncio.Lock()
    return daw._composerbench_transaction_lock


class RollbackError(RuntimeError):
    pass


class Transaction:
    def __init__(self, daw, sidecar=None, label='transaction'):
        self.daw, self.sidecar, self.label = daw, sidecar, label
        self.committed = False
        self.snapshot_id = None
        self._temp = None

    async def __aenter__(self):
        await transaction_lock(self.daw).acquire()
        try:
            self.snapshot_id = await self.daw.snapshot(self.label)
            if self.sidecar:
                self._temp = tempfile.TemporaryDirectory(prefix='composerbench-state-')
                capture_sidecar(self.sidecar.root, Path(self._temp.name) / 'state')
            return self
        except BaseException:
            transaction_lock(self.daw).release()
            if self._temp: self._temp.cleanup()
            raise

    def commit(self):
        self.committed = True

    async def _restore(self):
        try:
            await self.daw.restore_snapshot(self.snapshot_id)
            if self._temp:
                restore_sidecar(self.sidecar.root, Path(self._temp.name) / 'state')
        except Exception as e:
            raise RollbackError(f'restore failed for {self.snapshot_id}: {e}') from e

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None or not self.committed:
                task = asyncio.create_task(self._restore())
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
        finally:
            if self._temp: self._temp.cleanup()
            transaction_lock(self.daw).release()
