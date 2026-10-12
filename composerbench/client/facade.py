"""ComposerBench client facade (spec §95).

    client = ComposerBench.connect_fake()                # tests / offline
    client = ComposerBench.connect_magda(cli, project)   # real workstation

One object exposing project/tracks/edits/render + sidecar intent/plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from composerbench.adapter.base import DawAdapter, RenderRequest
from composerbench.adapter.fake import FakeDawAdapter
from composerbench.composition.state import CompositionPlan, Intent, SidecarStore
from composerbench.model import Clip, Device, Note, ProjectInfo, Track


class ComposerBench:
    def __init__(self, daw: DawAdapter, sidecar: SidecarStore):
        self.daw = daw
        self.sidecar = sidecar
        self._daw_adapter_type = type(daw)

    @classmethod
    def connect_fake(cls, project_dir: Path | str = ".") -> "ComposerBench":
        root = Path(project_dir)
        state = root / ".musicbench" / "fake_state.json"
        return cls(FakeDawAdapter(state_file=state), SidecarStore(root))

    @classmethod
    def connect_magda(cls, cli_path: Path | str, project_path: Path | str,
                      serve: bool = False) -> "ComposerBench":
        if serve:
            from composerbench.adapter.magda_serve import MagdaServeAdapter
            daw = MagdaServeAdapter(cli_path, project_path)
        else:
            from composerbench.adapter.magda import MagdaCliAdapter
            daw = MagdaCliAdapter(cli_path, project_path)
        return cls(daw, SidecarStore(daw.project.parent))

    def capabilities(self) -> dict[str, bool]:
        return self.daw.capabilities().to_dict()

    async def features(self) -> dict:
        """Capabilities plus the engine's protocol features (starts the engine
        for real adapters so the answer reflects the actual binary)."""
        probe = getattr(self.daw, 'features', None)
        extra = await probe() if probe else {}
        return {**self.daw.capabilities().to_dict(), 'engine': extra,
                'adapter': type(self.daw).__name__}

    @property
    def handles(self):
        from composerbench.client.handles import Handles
        return Handles(self.sidecar.root)

    async def _track_ref(self, ref):
        if not ref or not ref.startswith('mb:'): return ref
        descriptor = self.handles.lookup(ref, 'track')
        tracks = [t for t in await self.daw.get_tracks() if t.name == descriptor['name']]
        if len(tracks) != 1: raise LookupError('stable track is absent or ambiguous; inspect project')
        return tracks[0].id

    async def _entity_ref(self, track, ref, kind):
        if not ref or not ref.startswith('mb:'): return ref
        descriptor = self.handles.lookup(ref, kind)
        owner = await self.daw.get_track(track)
        if owner.name != descriptor['track']: raise LookupError('handle belongs to another track')
        items = await (self.daw.get_clips(track) if kind == 'clip' else self.daw.get_devices(track))
        matches = [item for item in items if item.name == descriptor['name']]
        index = descriptor['ordinal']
        if index >= len(matches): raise LookupError(f'stable {kind} is absent; inspect track')
        return matches[index].id

    async def _resolve_actions(self, actions):
        resolved = []
        for action in actions:
            a = action.model_dump() if hasattr(action, 'model_dump') else dict(action)
            a['track'] = await self._track_ref(a.get('track'))
            for key, kind in [('clip', 'clip'), ('device', 'device')]:
                if a.get(key): a[key] = await self._entity_ref(a['track'], a[key], kind)
            resolved.append(a)
        return resolved

    # ---- queries ----------------------------------------------------------------
    async def project(self) -> ProjectInfo:
        return await self.daw.get_project()

    async def tracks(self) -> list[Track]:
        tracks = await self.daw.get_tracks()
        for track in tracks:
            track.stable_id = self.handles.bind('track', track.name)
        return tracks

    async def track(self, ref: str) -> Track:
        track = await self.daw.get_track(await self._track_ref(ref))
        track.stable_id = self.handles.bind('track', track.name)
        return track

    async def clips(self, track: str | None = None) -> list[Clip]:
        clips = await self.daw.get_clips(await self._track_ref(track))
        for clip in clips:
            owner = await self.daw.get_track(clip.track_id)
            clip.stable_id = self.handles.bind('clip', clip.name, owner.name)
        return clips

    async def notes(self, track: str, clip: str | None = None) -> list[Note]:
        track = await self._track_ref(track)
        return await self.daw.get_notes(track, await self._entity_ref(track, clip, 'clip'))

    async def devices(self, track: str) -> list[Device]:
        track = await self._track_ref(track)
        owner = await self.daw.get_track(track)
        devices = await self.daw.get_devices(track)
        counts = {}
        for device in devices:
            ordinal = counts.get(device.name, 0)
            device.stable_id = self.handles.bind('device', device.name, owner.name, ordinal)
            counts[device.name] = ordinal + 1
        return devices

    # ---- edits -------------------------------------------------------------------
    async def create_track(self, name: str) -> Track:
        result = await self.batch([{'type': 'create_track', 'name': name}])
        return await self.track(result['results'][0]['id'])

    async def create_clip(self, track: str, name: str, start: float, length: float) -> Clip:
        result = await self.batch([{'type': 'create_clip', 'track': track, 'name': name,
                                    'start_beats': start, 'length_beats': length}])
        return next(c for c in await self.clips(track) if c.id == result['results'][0]['id'])

    async def add_notes(self, track: str, clip: str | None = None,
                        notes: Sequence[Note] = ()) -> int:
        track = await self._track_ref(track)
        clip = await self._entity_ref(track, clip, 'clip')
        tid = (await self.daw.get_track(track)).id
        resolved = await self.daw.get_clips(tid)
        if not resolved:
            raise LookupError(f"no clip on track {track!r}; create one first")
        if clip is None:
            target = resolved[-1]
        else:
            key = clip.removeprefix("clip:")
            target = next((c for c in resolved if c.id == clip or c.id.removeprefix("clip:") == key or c.name == clip), None)
            if target is None:
                raise LookupError(f"clip {clip!r} not found on track {track!r}")
        result = await self.batch([{'type': 'add_notes', 'track': tid, 'clip': target.id,
                                    'notes': list(notes)}])
        return result['results'][0]['added']

    async def set_notes(self, track: str, clip: str | None = None,
                        notes: Sequence[Note] = ()) -> int:
        """Replace every note in a clip in one request (one undo step).

        This is the fast path for writing or rewriting a whole part: the
        engine syncs the clip once instead of once per note.
        """
        track = await self._track_ref(track)
        clip = await self._entity_ref(track, clip, 'clip')
        tid = (await self.daw.get_track(track)).id
        resolved = await self.daw.get_clips(tid)
        if not resolved:
            raise LookupError(f"no clip on track {track!r}; create one first")
        if clip is None:
            if len(resolved) != 1:
                raise LookupError('track has several clips; name the clip to replace')
            target = resolved[0]
        else:
            key = clip.removeprefix("clip:")
            target = next((c for c in resolved if c.id == clip or c.id.removeprefix("clip:") == key
                           or c.name == clip), None)
            if target is None:
                raise LookupError(f"clip {clip!r} not found on track {track!r}")
        result = await self.batch([{'type': 'set_notes', 'track': tid, 'clip': target.id,
                                    'notes': list(notes)}])
        return result['results'][0]['notes']

    def render(self, request: RenderRequest):
        return self.daw.render(request)

    async def audio_inspect(self, **arguments):
        import asyncio
        from composerbench.analysis.audition import inspect_audio
        return await asyncio.to_thread(inspect_audio, **arguments)

    async def audio_visual_review(self, **arguments):
        import asyncio
        from composerbench.analysis.visual import visual_review
        arguments.setdefault('output_dir', self.sidecar.renders)
        return await asyncio.to_thread(visual_review, **arguments)

    async def audio_compare(self, **arguments):
        import asyncio
        from composerbench.analysis.audition import compare_audio
        arguments.setdefault('output_dir', self.sidecar.renders)
        return await asyncio.to_thread(compare_audio, **arguments)

    async def audio_resample(self, **arguments):
        import asyncio
        from composerbench.production import resample_audio
        arguments.setdefault('output_dir', self.sidecar.renders)
        return await asyncio.to_thread(resample_audio, **arguments)

    # ---- artistic state ------------------------------------------------------------
    async def read_intent(self) -> Intent:
        return self.sidecar.read_intent()

    async def write_intent(self, intent: Intent) -> Path:
        return self.sidecar.write_intent(intent)

    async def read_plan(self) -> CompositionPlan:
        return self.sidecar.read_plan()

    async def write_plan(self, plan: CompositionPlan) -> Path:
        return self.sidecar.write_plan(plan)

    async def initialize(self):
        init = getattr(self.daw, 'initialize', None)
        if init:
            if self.daw.project.exists():
                raise ValueError('project already exists; inspect or edit it instead')
            await init()
            self.sidecar = SidecarStore(self.daw.project.parent)
        return await self.project()

    async def close(self):
        shutdown = getattr(self.daw, 'shutdown', None)
        if shutdown:
            if getattr(self.daw, '_serve_proc', None) is None:
                await self.daw._drop_session()
            else:
                await shutdown()

    async def parameters(self, track: str, device: str):
        track = await self._track_ref(track)
        return await self.daw.get_parameters(track, await self._entity_ref(track, device, 'device'))

    async def describe_parameter(self, track: str, device: str, parameter: str):
        """Range, unit, choices and current meaning of one parameter."""
        track = await self._track_ref(track)
        device = await self._entity_ref(track, device, 'device')
        describe = getattr(self.daw, 'describe_parameter', None)
        if describe is not None:
            return await describe(track, device, parameter)
        from composerbench.params import describe as describe_param, find
        key, meta = find(await self.daw.get_parameters(track, device), parameter)
        return {'index': key, **describe_param(meta)}

    async def plugins(self, format_filter: str | None = None, instruments: bool | None = None):
        """Scanned external plugins (AU/VST3/LV2) the engine can host."""
        lister = getattr(self.daw, 'list_plugins', None)
        if lister is None:
            from composerbench.errors import UnsupportedOperation
            raise UnsupportedOperation(reason='external plugins need the MAGDA serve adapter')
        plugs = await lister(format_filter)
        if instruments is not None:
            plugs = [p for p in plugs if bool(p.get('isInstrument')) == instruments]
        return plugs

    async def add_plugin(self, track: str, plugin: str, instrument: bool | None = None):
        """Host an external plugin on a track, by exact id or unique name."""
        track_id = await self._track_ref(track)
        plugs = await self.plugins()
        hits = [p for p in plugs if plugin in (p.get('id'), p.get('name'))]
        if not hits:
            hits = [p for p in plugs if plugin.lower() in str(p.get('name', '')).lower()]
        if len(hits) != 1:
            from composerbench.errors import EntityNotFound
            raise EntityNotFound('plugin', plugin, [p.get('name') for p in hits or plugs][:20])
        desc = hits[0]
        as_instrument = bool(desc.get('isInstrument')) if instrument is None else instrument
        device = await self.daw.add_plugin(track_id, desc['id'], as_instrument=as_instrument)
        await self.daw.save_project()
        return {'device_id': device, 'plugin': desc.get('name'), 'format': desc.get('format'),
                'instrument': as_instrument,
                'persistent': self.daw.capabilities().plugin_persistence}

    async def save(self):
        await self.daw.save_project()
        return {'saved': str(getattr(self.daw, 'project', ''))}

    async def batch(self, actions, timeout_seconds: float = 120):
        import asyncio
        from composerbench.agent.actions import preflight, apply
        from composerbench.transactions import Transaction
        if not 0 < timeout_seconds <= 3600: raise ValueError('timeout must be in (0, 3600] seconds')
        async with asyncio.timeout(timeout_seconds):
            async with Transaction(self.daw, self.sidecar, 'batch') as tx:
                # One save for the whole batch instead of one per engine command
                # (engines without the save flag simply keep saving each time).
                previous = getattr(self.daw, 'autosave', None)
                if previous is not None:
                    self.daw.autosave = False
                try:
                    validated = await preflight(self.daw, await self._resolve_actions(actions))
                    results = [await apply(self.daw, a) for a in validated]
                    await self._bind_results(validated, results)
                finally:
                    if previous is not None:
                        self.daw.autosave = previous
                await self.daw.save_project()
                tx.commit()
                return {'results': results, 'snapshot_id': tx.snapshot_id,
                        'project': (await self.project()).model_dump()}

    async def _bind_results(self, actions, results):
        for a, result in zip(actions, results):
            if a.type == 'create_track':
                result['stable_id'] = (await self.track(result['id'])).stable_id
            elif a.type == 'create_clip':
                result['stable_id'] = next(c.stable_id for c in await self.clips(a.track) if c.id == result['id'])
            elif a.type in ('load_device', 'add_fx'):
                devices = await self.devices(a.track)
                matches = [d for d in devices if d.id == result.get('id', result.get('device_id')) or d.name == a.name]
                if matches:
                    result['stable_id'] = matches[-1].stable_id

    async def set_volume(self, track, volume):
        return await self.batch([{'type': 'set_volume', 'track': track, 'volume': volume}])

    async def load_device(self, track, device):
        return await self.batch([{'type': 'load_device', 'track': track, 'name': device}])

    async def add_fx(self, track, name):
        return await self.batch([{'type': 'add_fx', 'track': track, 'name': name}])

    async def set_parameter(self, track, device, parameter, value):
        return await self.batch([{'type': 'set_parameter', 'track': track,
                                  'device': str(device), 'parameter': str(parameter), 'value': value}])

    def roles(self):
        from composerbench.semantics.registry import load_default
        return [r.model_dump() for r in load_default().roles.values()]

    async def apply_role(self, track, role):
        return await self.batch([{'type': 'apply_role', 'track': track, 'name': role}])

    async def snapshot(self, label):
        import hashlib
        from composerbench.transactions import capture_sidecar, transaction_lock
        async with transaction_lock(self.daw):
            sid = await self.daw.snapshot(label)
            target = self.sidecar.snapshots / ('state-' + hashlib.sha256(sid.encode()).hexdigest())
            capture_sidecar(self.sidecar.root, target)
            return sid

    async def restore_snapshot(self, sid):
        import hashlib
        from composerbench.transactions import restore_sidecar, transaction_lock
        async with transaction_lock(self.daw):
            await self.daw.restore_snapshot(sid)
            target = self.sidecar.snapshots / ('state-' + hashlib.sha256(sid.encode()).hexdigest())
            if target.exists(): restore_sidecar(self.sidecar.root, target)

    async def critique(self, path):
        from composerbench.agent.loop import RevisionLoop, CriticConfig
        plan, intent = await self.read_plan(), await self.read_intent()
        loop = RevisionLoop(self.daw, plan, lambda _: [], self.sidecar.renders,
                            critic=CriticConfig(enabled=True, intent_emotion=intent.emotion,
                                                energy_targets={s.name: s.target_energy for s in plan.sections}),
                            intent=intent)
        return await loop._measure(Path(path))

    async def revise(self, goal, actions, objectives=None, timeout_seconds=120,
                     critic=False, auto_fix=False, max_peak_dbfs=-0.5,
                     audition_start_seconds=None, audition_duration_seconds=8.0):
        from composerbench.agent.loop import RevisionLoop, LoopBudget, CriticConfig, RenderConstraints
        async def propose(_):
            return await self._resolve_actions(actions)
        loop = RevisionLoop(self.daw, await self.read_plan(), propose,
                            self.sidecar.renders, budget=LoopBudget(1, timeout_seconds),
                            render_constraints=RenderConstraints(max_peak_dbfs=max_peak_dbfs),
                            critic=CriticConfig(enabled=critic, auto_fix=auto_fix),
                            objectives=objectives, intent=await self.read_intent(), sidecar=self.sidecar,
                            audition_start_seconds=audition_start_seconds,
                            audition_duration_seconds=audition_duration_seconds)
        return (await loop.run([goal]))[0].to_dict()

    def revision_history(self):
        import json
        if not self.sidecar.traces.exists(): return []
        return [json.loads(p.read_text()) for p in sorted(self.sidecar.traces.glob('*.json'), key=lambda p: p.stat().st_mtime)]
