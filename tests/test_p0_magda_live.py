from pathlib import Path
import pytest
from composerbench.mcp.server import MCPServer, ServerConfig
from composerbench.client import ComposerBench
from test_p0_mcp import invoke

CLI=Path('~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli').expanduser()
pytestmark=pytest.mark.skipif(not CLI.exists(),reason='MAGDA CLI unavailable')


async def test_persistent_engine_restore_and_public_tools(tmp_path):
    client=ComposerBench.connect_magda(CLI,tmp_path/'p.mgd',serve=True)
    server=MCPServer(client)
    try:
        await invoke(server,'music_project_init')
        await invoke(server,'music_batch_edit',actions=[
            {'type':'create_track','name':'lead'},
            {'type':'load_device','track':'lead','name':'4osc'},
            {'type':'create_clip','track':'lead','name':'phrase','length_beats':2},
            {'type':'add_notes','track':'lead','clip':'phrase','notes':[{'start':0,'duration':1,'pitch':60}]},
        ])
        devices=(await invoke(server,'music_devices_inspect',track='lead'))['devices']
        assert devices
        # Parameter IDs come from live discovery, never guessed names/ranges.
        device=devices[0]['stable_id']
        params=(await invoke(server,'music_parameters_inspect',track='lead',device=device))['parameters']
        if params:
            key,p=next(iter(params.items()))
            await invoke(server,'music_parameter_set',track='lead',device=device,parameter=str(key),value=p['value'])
        lead_ref=(await invoke(server,'music_project_inspect'))['tracks'][0]['stable_id']
        sid=(await invoke(server,'music_snapshot_create',label='baseline'))['snapshot_id']
        for _ in range(2):
            await invoke(server,'music_volume_set',track='lead',volume=.4)
            assert (await invoke(server,'music_track_inspect',track=lead_ref))['volume']==pytest.approx(.4)
            await invoke(server,'music_snapshot_restore',snapshot_id=sid)
            state=await invoke(server,'music_track_inspect',track=lead_ref)
            assert state['volume']==pytest.approx(1)
            assert state['symbolic_stats']['note_count']==1
        original = client.daw.set_track_volume
        async def failing_write(track, volume):
            await original(track, volume)
            raise RuntimeError('failure after engine mutation')
        client.daw.set_track_volume = failing_write
        failed = await server.call_tool('music_volume_set', {'track': lead_ref, 'volume': .2})
        client.daw.set_track_volume = original
        assert failed['isError']
        assert (await invoke(server,'music_track_inspect',track=lead_ref))['volume']==pytest.approx(1)
        render=await invoke(server,'music_render',output_path=str(tmp_path/'out.wav'),length_beats=2)
        assert render['audio']['peak_dbfs'] > -60
        await invoke(server,'music_fx_add',track='lead',name='magda_reverb')
        assert len((await invoke(server,'music_devices_inspect',track='lead'))['devices'])>len(devices)
        await invoke(server,'music_plan_update',plan={'sections':[]})
        revision=await invoke(server,'music_revision_run',goal='lower the level',
            actions=[{'type':'set_volume','track':'lead','volume':.5}],
            objectives=[{'metric':'rms_dbfs','direction':'decrease','min_improvement':1}])
        assert revision['decision']=='accepted',revision
        assert revision['evaluation']['improved']
    finally:
        await client.close()
