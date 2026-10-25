import asyncio

import numpy as np
from test_p0_audio import wav

from composerbench.adapter.fake import FakeDawAdapter
from composerbench.agent.loop import Action, CriticConfig, LoopBudget, RevisionLoop
from composerbench.composition.state import CompositionPlan, Section


class AudioDaw(FakeDawAdapter):
    async def render(self, request):
        t = await self.get_track('lead')
        tone = .15*t.volume*np.sin(2*np.pi*440*np.arange(44100)/44100)
        return wav(request.output_path, tone, sr=44100)


async def seeded(daw):
    t=await daw.create_track('lead')
    await daw.create_clip(t.id,'clip',0,2)
    return daw


async def test_gain_only_change_cannot_win_brightness_goal(tmp_path):
    daw = await seeded(AudioDaw())
    loop=RevisionLoop(daw,CompositionPlan(),lambda _: [Action(type='set_volume',track='lead',volume=1.5)],tmp_path,
                      objectives=[{'metric':'centroid_hz','direction':'increase','min_improvement':20}])
    r=(await loop.run(['make brighter']))[0]
    assert r.decision == 'rejected'
    assert (await daw.get_track('lead')).volume == 1
    assert r.evaluation['objectives'][0]['improved'] is False
    assert r.baseline_render_path and r.render_path
    assert list((tmp_path/'traces').glob('*.json'))


async def test_explicit_level_objective_and_full_proposer_context(tmp_path):
    daw=await seeded(AudioDaw())
    def propose(world):
        assert {'state','intent','plan','baseline','history','capabilities','objectives'} <= world.keys()
        return [Action(type='set_volume',track='lead',volume=1.5)]
    loop=RevisionLoop(daw,CompositionPlan(),propose,tmp_path,
                      objectives=[{'metric':'rms_dbfs','direction':'increase','min_improvement':1}])
    r=(await loop.run(['raise level']))[0]
    assert r.decision == 'accepted'
    assert r.evaluation['improved']
    assert r.state_diff['after']['tracks'][0]['volume'] == 1.5


async def test_unscored_audio_revision_requires_objectives(tmp_path):
    daw=await seeded(AudioDaw())
    loop=RevisionLoop(daw,CompositionPlan(),lambda _: [],tmp_path)
    r=(await loop.run(['better']))[0]
    assert r.decision == 'rejected'
    assert r.evaluation['status'] == 'needs_goal_objectives'


async def test_autofix_is_remeasured_and_rejected_on_clipping(tmp_path, monkeypatch):
    from composerbench.critique import mixer
    monkeypatch.setattr(mixer,'fixes_for_findings',lambda *a: [Action(type='set_volume',track='lead',volume=2)])
    class ClippingDaw(AudioDaw):
        async def render(self, request):
            t=await self.get_track('lead')
            return wav(request.output_path,np.full(44100,1.1 if t.volume==2 else .1),sr=44100,floating=True)
    daw=await seeded(ClippingDaw())
    loop=RevisionLoop(daw,CompositionPlan(),lambda _: [],tmp_path,critic=CriticConfig(enabled=True),
                      objectives=[{'metric':'rms_dbfs','direction':'increase'}])
    r=(await loop.run(['louder']))[0]
    assert r.decision == 'rejected'
    assert r.evaluation['candidate']['metrics']['peak_dbfs'] > 0
    assert r.render_path.endswith('fixed.wav')
    assert (await daw.get_track('lead')).volume == 1


async def test_loop_timeout_rolls_back_and_records(tmp_path):
    daw=await seeded(AudioDaw())
    async def slow(_):
        await daw.set_track_volume('lead',1.5)
        await asyncio.sleep(10)
    loop=RevisionLoop(daw,CompositionPlan(),slow,tmp_path,budget=LoopBudget(1,.1))
    r=(await loop.run(['slow']))[0]
    assert r.decision == 'rejected' and 'TimeoutError' in r.reason
    assert (await daw.get_track('lead')).volume == 1


async def test_critic_receives_zero_based_meter_aware_bounds(tmp_path, monkeypatch):
    import composerbench.analysis.features as features
    original=features.section_features
    calls=[]
    def capture(path,bounds,bpm):
        calls.append(bounds)
        return original(path,bounds,bpm)
    monkeypatch.setattr(features,'section_features',capture)
    daw=await seeded(AudioDaw())
    plan=CompositionPlan(beats_per_bar=3,sections=[Section('intro',1,1)])
    loop=RevisionLoop(daw,plan,lambda _: [],tmp_path,critic=CriticConfig(enabled=True,auto_fix=False))
    await loop.run(['test'])
    assert calls[0] == [(0,3)]


async def test_save_failure_never_records_acceptance(tmp_path):
    class SaveFails(AudioDaw):
        async def save_project(self): raise RuntimeError('disk full')
    daw=await seeded(SaveFails())
    loop=RevisionLoop(daw,CompositionPlan(),lambda _: [Action(type='set_volume',track='lead',volume=1.5)],tmp_path,
                      objectives=[{'metric':'rms_dbfs','direction':'increase','min_improvement':1}])
    rec=(await loop.run(['louder']))[0]
    assert rec.decision=='rejected' and 'disk full' in rec.reason
    assert (await daw.get_track('lead')).volume==1
