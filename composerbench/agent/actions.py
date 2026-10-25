"""Validated public edit vocabulary and batch preflight."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from composerbench.model import Note


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    type: Literal['create_track', 'create_clip', 'add_notes', 'set_notes', 'transpose_clip',
                  'set_tempo', 'load_device', 'set_volume', 'add_fx',
                  'set_parameter', 'apply_role']
    track: str | None = None
    clip: str | None = None
    name: str | None = None
    start_beats: float = Field(default=0, ge=0)
    length_beats: float = Field(default=4, gt=0)
    bpm: float | None = Field(default=None, gt=0)
    volume: float = Field(default=1, ge=0, le=2)
    notes: list[Note] = Field(default_factory=list)
    semitones: int = 0
    device: str | None = None
    parameter: str | None = None
    value: float | str | None = None   # number, or a choice label such as "Hall"

    @model_validator(mode='after')
    def required_fields(self):
        if self.type == 'set_tempo':
            if self.bpm is None: raise ValueError('set_tempo requires bpm')
        elif self.type == 'create_track':
            if not (self.name or self.track): raise ValueError('create_track requires name')
        elif not self.track:
            raise ValueError(f'{self.type} requires track')
        if self.type in ('load_device', 'add_fx', 'apply_role') and not self.name:
            raise ValueError(f'{self.type} requires name')
        if self.type == 'set_parameter' and (not self.device or self.parameter is None or self.value is None):
            raise ValueError('set_parameter requires device, parameter and value')
        return self


def clip_for(clips, ref, start):
    if ref is not None:
        matches = [c for c in clips if c.id == ref or c.id.removeprefix('clip:') == ref.removeprefix('clip:') or c.name == ref]
    else:
        matches = [c for c in clips if c.start <= start < c.start + c.length]
    if len(matches) != 1:
        raise LookupError(f'clip reference must resolve exactly once: {ref!r}')
    return matches[0]


async def preflight(daw, actions) -> list[Action]:
    from composerbench.model import Clip
    actions = [Action.model_validate(a.model_dump() if isinstance(a, Action) else a) for a in actions]
    tracks = await daw.get_tracks()
    known = {ref: t.id for t in tracks for ref in (t.id, t.name, f'track:{t.name}')}
    clips = {t.id: await daw.get_clips(t.id) for t in tracks}
    caps = daw.capabilities()
    for a in actions:
        needed = {'add_fx': 'effects', 'set_parameter': 'plugin_parameters',
                  'apply_role': 'semantic_roles', 'add_notes': 'midi_edit',
                  'set_notes': 'bulk_notes',
                  'create_clip': 'midi_edit', 'transpose_clip': 'midi_edit'}.get(a.type)
        if needed and not getattr(caps, needed):
            from composerbench.errors import UnsupportedOperation
            raise UnsupportedOperation(reason=f'{a.type} unavailable', capability=needed)
        if a.type == 'create_track':
            name = a.name or a.track
            if name in known or f'track:{name}' in known: raise ValueError(f'duplicate track {name}')
            tid = f'track:{name}'
            known.update({name: tid, tid: tid}); clips[tid] = []
            continue
        if a.type == 'set_tempo': continue
        if a.type == 'apply_role':
            from composerbench.semantics.registry import load_default
            load_default().get(a.name)  # recipe and aliases must exist before edits
            if a.track not in known:
                tid = f'track:{a.track}'
                known.update({a.track: tid, tid: tid}); clips[tid] = []
        if a.track not in known: raise LookupError(f'unknown track {a.track!r}')
        tid = known[a.track]
        if a.type == 'load_device':
            guard = getattr(daw, '_guard_instrument', None)
            if guard: guard(a.name)
        if a.type == 'create_clip':
            name = a.name or f'clip-{a.start_beats}'
            if any(c.name == name for c in clips[tid]): raise ValueError(f'duplicate clip {name}')
            clips[tid].append(Clip(id=f'clip:{name}', name=name, track_id=tid,
                                   start=a.start_beats, length=a.length_beats))
        if a.type in ('add_notes', 'set_notes', 'transpose_clip'):
            ref_start = a.notes[0].start if (a.clip is None and a.notes) else a.start_beats
            c = clip_for(clips[tid], a.clip, ref_start)
            if any(not c.start <= n.start < c.start+c.length for n in a.notes):
                raise ValueError(f'note starts outside clip {c.name}')
        if a.type == 'set_parameter':
            from composerbench.params import UnknownParameterLabel, find, resolve_value
            params = await daw.get_parameters(tid, a.device)
            key, param = find(params, a.parameter)   # ParameterNotFound is a LookupError
            # labels ("Hall", "1/8.") resolve here; numbers are range-checked
            try:
                a.value = resolve_value(param, a.value)
            except UnknownParameterLabel:
                # hosted plugins may only reveal their labels on request
                describe = getattr(daw, 'describe_parameter', None)
                info = await describe(tid, a.device, key) if describe else {}
                if not info.get('choices'):
                    raise
                a.value = resolve_value({**param, **info}, a.value)
            a.parameter = str(key)
    return actions


async def apply(daw, a: Action):
    if a.type == 'create_track': return (await daw.create_track(a.name or a.track)).model_dump()
    if a.type == 'set_tempo':
        await daw.set_project_tempo(a.bpm); return {'tempo_bpm': a.bpm}
    if a.type == 'apply_role':
        from composerbench.semantics.registry import load_default
        reg = load_default()
        from composerbench.errors import EntityNotFound
        try:
            track_name = (await daw.get_track(a.track)).name
        except (EntityNotFound, LookupError):
            track_name = a.track
        result = await daw.apply_role(track_name, reg.get(a.name), reg)
        if any('?' in s for s in result.get('steps', [])):
            raise ValueError(f'role parameters could not be applied: {result}')
        return result
    t = await daw.get_track(a.track)
    if a.type == 'create_clip':
        return (await daw.create_clip(t.id, a.name or f'clip-{a.start_beats}', a.start_beats, a.length_beats)).model_dump()
    if a.type == 'load_device': return (await daw.load_device(t.id, a.name)).model_dump()
    if a.type == 'set_volume':
        await daw.set_track_volume(t.id, a.volume); return {'track_id': t.id, 'volume': a.volume}
    if a.type == 'add_fx': return {'device_id': str(await daw.add_fx(t.id, a.name))}
    if a.type == 'set_parameter':
        await daw.set_parameter(t.id, a.device, a.parameter, a.value)
        return {'device_id': a.device, 'parameter': a.parameter, 'value': a.value}
    ref_start = a.notes[0].start if (a.clip is None and a.notes) else a.start_beats
    c = clip_for(await daw.get_clips(t.id), a.clip, ref_start)
    if a.type == 'add_notes': return {'added': await daw.add_notes(t.id, c.id, a.notes)}
    if a.type == 'set_notes': return {'notes': await daw.set_notes(t.id, c.id, a.notes), 'clip': c.id}
    return {'changed': await daw.modify_notes(t.id, c.id, list(range(len(c.notes))), transpose=a.semitones)}
