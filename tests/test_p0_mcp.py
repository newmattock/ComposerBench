import jsonschema
import pytest
from composerbench.mcp.server import MCPServer, ServerConfig, build_client, TOOLS


async def invoke(server, tool_name, **args):
    r=await server.call_tool(tool_name,args)
    assert not r['isError'],r
    spec=next(t for t in TOOLS if t['name']==tool_name)
    jsonschema.validate(r['structuredContent'],spec['outputSchema'])
    return r['structuredContent']


async def test_public_edit_render_restore_flow(tmp_path):
    server=MCPServer(build_client(ServerConfig(project_path=str(tmp_path))))
    await invoke(server,'music_batch_edit',actions=[
        {'type':'create_track','name':'lead'},
        {'type':'load_device','track':'lead','name':'synth'},
        {'type':'create_clip','track':'lead','name':'phrase','length_beats':2},
        {'type':'add_notes','track':'lead','clip':'phrase','notes':[{'start':0,'duration':1,'pitch':60}]},
    ])
    devices=(await invoke(server,'music_devices_inspect',track='lead'))['devices']
    device=devices[0]['id']
    await invoke(server,'music_parameter_set',track='lead',device=device,parameter='cutoff',value=.8)
    p=(await invoke(server,'music_parameters_inspect',track='lead',device=device))['parameters']
    assert p['cutoff']['value']==.8
    sid=(await invoke(server,'music_snapshot_create',label='approved'))['snapshot_id']
    await invoke(server,'music_intent_update',intent={'title':'changed'})
    await invoke(server,'music_volume_set',track='lead',volume=.5)
    await invoke(server,'music_snapshot_restore',snapshot_id=sid)
    assert (await invoke(server,'music_track_inspect',track='lead'))['volume']==1
    assert (await invoke(server,'music_intent_read'))['title']=='untitled'
    render=await invoke(server,'music_render',output_path=str(tmp_path/'audition.wav'))
    assert render['audio']['peak_dbfs'] > -60
    await invoke(server,'music_plan_update',plan={'sections':[]})
    revision=await invoke(server,'music_revision_run',goal='brighter',actions=[],
                          objectives=[{'metric':'centroid_hz','direction':'increase','min_improvement':20}])
    assert revision['decision']=='rejected'
    assert (await invoke(server,'music_revision_history'))['revisions'][0]['goal']=='brighter'


async def test_invalid_batch_returns_actionable_structured_error(tmp_path):
    server=MCPServer(build_client(ServerConfig(project_path=str(tmp_path))))
    r=await server.call_tool('music_batch_edit',{'actions':[{'type':'create_track','name':'a'},{'type':'set_volume','track':'a','volume':20}]})
    assert r['isError'] and 'error' in r['structuredContent']
    assert (await invoke(server,'music_project_inspect'))['tracks']==[]


def test_persistent_backend_is_selectable(tmp_path):
    cli=tmp_path/'cli';cli.touch()
    client=build_client(ServerConfig(adapter='magda_serve',cli_path=str(cli),project_path=str(tmp_path/'p.mgd')))
    assert client.capabilities()['plugin_parameters']
    assert client.capabilities()['effects']
    assert client.capabilities()['semantic_roles']
    assert not client.capabilities()['automation']


def test_all_schemas_are_valid():
    for tool in TOOLS:
        jsonschema.Draft202012Validator.check_schema(tool['inputSchema'])
        jsonschema.Draft202012Validator.check_schema(tool['outputSchema'])


async def test_stable_handles_survive_engine_renumbering_and_reconnect(tmp_path):
    from composerbench.client import ComposerBench
    mb=ComposerBench.connect_fake(tmp_path)
    t=await mb.create_track('lead')
    c=await mb.create_clip(t.stable_id,'phrase',0,2)
    await mb.add_notes(t.stable_id,c.stable_id,notes=[{'start':0,'duration':1,'pitch':60}])
    result=await mb.load_device(t.stable_id,'synth')
    device=result['results'][0]['stable_id']
    # Simulate the backend assigning new numeric/opaque handles on reload.
    daw=mb.daw
    old=t.id
    track=daw._tracks.pop(old); track.id='engine:99';daw._tracks[track.id]=track
    for clip in daw._clips.values(): clip.track_id=track.id
    daw._save_state()
    reopened=ComposerBench.connect_fake(tmp_path)
    assert (await reopened.track(t.stable_id)).id=='engine:99'
    await reopened.set_volume(t.stable_id,.4)
    await reopened.set_parameter(t.stable_id,device,'cutoff',.6)
    assert (await reopened.parameters(t.stable_id,device))['cutoff']['value']==.6
    assert len(await reopened.notes(t.stable_id,c.stable_id))==1
