import asyncio
from pathlib import Path

import pytest

from composerbench.adapter.fake import FakeDawAdapter
from composerbench.adapter.magda import MagdaCliAdapter
from composerbench.client import ComposerBench
from composerbench.composition.state import Intent, SidecarStore
from composerbench.transactions import Transaction


class FailingDaw(FakeDawAdapter):
    async def create_track(self, name):
        if name == 'fail': raise RuntimeError('injected failure')
        return await super().create_track(name)


async def test_batch_validates_before_first_edit(tmp_path):
    mb = ComposerBench(FakeDawAdapter(), SidecarStore(tmp_path))
    with pytest.raises(Exception):
        await mb.batch([{'type':'create_track','name':'first'}, {'type':'set_volume','track':'missing','volume':1}])
    assert await mb.tracks() == []
    assert mb.daw._revision == 0


async def test_runtime_failure_restores_disk_and_artistic_state(tmp_path):
    daw = FailingDaw(state_file=tmp_path/'.musicbench/fake_state.json')
    mb = ComposerBench(daw, SidecarStore(tmp_path))
    await mb.write_intent(Intent(title='original'))
    with pytest.raises(RuntimeError):
        async with Transaction(daw, mb.sidecar):
            await daw.create_track('partial')
            await mb.write_intent(Intent(title='changed'))
            await daw.create_track('fail')
    assert await mb.tracks() == []
    assert await FakeDawAdapter(state_file=daw.state_file).get_tracks() == []
    assert (await mb.read_intent()).title == 'original'


@pytest.mark.parametrize('cancel', [True, False])
async def test_cancel_and_timeout_restore(tmp_path, cancel):
    daw = FakeDawAdapter()
    ready = asyncio.Event()
    async def edit():
        async with Transaction(daw):
            await daw.create_track('partial')
            ready.set()
            await asyncio.sleep(20)
    task = asyncio.create_task(edit())
    await ready.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    else:
        with pytest.raises(TimeoutError): await asyncio.wait_for(task,.01)
    assert await daw.get_tracks() == []


async def test_real_file_snapshot_is_reusable_and_complete(tmp_path):
    cli = tmp_path/'cli'; cli.touch()
    project = tmp_path/'song.mgd'; project.write_text('original')
    daw = MagdaCliAdapter(cli, project)
    media = tmp_path/'song_Media'; media.mkdir(); (media/'sample.wav').write_bytes(b'original')
    state = tmp_path/'.musicbench'; state.mkdir()
    (state/'intent.yaml').write_text('title: original')
    sid = await daw.snapshot('before')
    for _ in range(2):
        project.write_text('changed'); (media/'sample.wav').write_bytes(b'changed')
        (state/'intent.yaml').write_text('title: changed'); (state/'devices.json').write_text('{}')
        await daw.restore_snapshot(sid)
        assert project.read_text() == 'original'
        assert (media/'sample.wav').read_bytes() == b'original'
        assert (state/'intent.yaml').read_text() == 'title: original'
        assert not (state/'devices.json').exists()
        assert (Path(sid).parent/'song_Media/sample.wav').exists()


async def test_snapshot_removes_media_created_after_snapshot(tmp_path):
    cli = tmp_path/'cli'; cli.touch()
    p=tmp_path/'p.mgd'; p.touch()
    daw=MagdaCliAdapter(cli,p)
    sid=await daw.snapshot('empty')
    media=tmp_path/'p_Media';media.mkdir()
    await daw.restore_snapshot(sid)
    assert not media.exists()
