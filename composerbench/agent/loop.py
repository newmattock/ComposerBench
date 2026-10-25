"""Transactional propose/render/compare loop with explicit evidence of improvement."""
from __future__ import annotations

import asyncio
import inspect
import json
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from composerbench.adapter.base import DawAdapter, RenderRequest
from composerbench.agent.actions import Action, preflight
from composerbench.agent.actions import apply as _apply
from composerbench.analysis.diff import diff_tracks
from composerbench.composition.state import CompositionPlan, Intent, SidecarStore
from composerbench.evaluation.comparison import Objective, compare
from composerbench.evaluation.constraints import (
    ConstraintResult,
    check_audible,
    check_energy_relation,
    check_no_vocals,
    check_peak_below,
)
from composerbench.transactions import RollbackError, Transaction


@dataclass
class RevisionRecord:
    index: int
    goal: str
    decision: str
    reason: str
    diff_summary: str
    render_path: str | None
    duration_ms: int
    constraint_results: list[dict] = field(default_factory=list)
    baseline_render_path: str | None = None
    evaluation: dict = field(default_factory=dict)
    actions: list[dict] = field(default_factory=list)
    state_diff: dict = field(default_factory=dict)
    snapshot_id: str | None = None

    def to_dict(self):
        d = asdict(self)
        d['constraints'] = d.pop('constraint_results')
        return d


Proposer = Callable[[dict[str, Any]], Awaitable[list[Action]] | list[Action]]


@dataclass
class LoopBudget:
    max_iterations: int = 8
    max_seconds: float = 600.0


@dataclass
class RenderConstraints:
    max_peak_dbfs: float | None = -0.5
    min_peak_dbfs: float | None = -60.0


@dataclass
class CriticConfig:
    enabled: bool = False
    section_bounds_beats: list[tuple[float, float]] = field(default_factory=list)
    intent_emotion: dict | None = None
    energy_targets: dict[str, float] | None = None
    role_of_track: dict[str, str] | None = None
    auto_fix: bool = True


class RevisionLoop:
    def __init__(self, daw: DawAdapter, plan: CompositionPlan, proposer: Proposer,
                 work_dir: Path, budget: LoopBudget | None = None,
                 render_audio: bool = True, render_constraints: RenderConstraints | None = None,
                 critic: CriticConfig | None = None, objectives: list[Objective | dict] | None = None,
                 intent: Intent | None = None, sidecar: SidecarStore | None = None,
                 audition_start_seconds: float | None = None, audition_duration_seconds: float = 8.0):
        self.daw, self.plan, self.proposer = daw, plan, proposer
        if audition_start_seconds is not None and (not math.isfinite(audition_start_seconds) or audition_start_seconds < 0):
            raise ValueError('audition start must be finite and nonnegative')
        if not math.isfinite(audition_duration_seconds) or not 0.4 <= audition_duration_seconds <= 30:
            raise ValueError('audition duration must be between 0.4 and 30 seconds')
        self.audition_start_seconds = audition_start_seconds
        self.audition_duration_seconds = audition_duration_seconds
        self.work_dir = Path(work_dir)
        self.budget = budget or LoopBudget()
        if not math.isfinite(self.budget.max_seconds) or self.budget.max_seconds <= 0 or self.budget.max_iterations < 0:
            raise ValueError('positive time budget and nonnegative iteration budget required')
        self.render_constraints = render_constraints or RenderConstraints()
        for limit in (self.render_constraints.max_peak_dbfs, self.render_constraints.min_peak_dbfs):
            if limit is not None and not math.isfinite(limit):
                raise ValueError("render limits must be finite or None")
        self.critic = critic
        self.objectives = [Objective.model_validate(o) for o in (objectives or [])]
        for o in self.objectives:
            if o.section and plan.section(o.section) is None:
                raise ValueError(f'unknown objective section {o.section}')
            if o.section and o.metric in ('peak_dbfs', 'crest_factor_db'):
                raise ValueError('peak/crest objectives currently require whole-project scope')
        self.intent = intent
        self.sidecar = sidecar
        self.history: list[RevisionRecord] = []
        self.prior_history = []
        if sidecar and sidecar.traces.exists():
            paths = sorted(sidecar.traces.glob('*.json'), key=lambda p: p.stat().st_mtime)
            self.prior_history = [json.loads(p.read_text()) for p in paths[-10:]]
        self.render_audio = render_audio and daw.capabilities().offline_render
        self.run_id = uuid.uuid4().hex

    async def run(self, goals: list[str]) -> list[RevisionRecord]:
        deadline = time.monotonic() + self.budget.max_seconds
        for i, goal in enumerate(goals[:self.budget.max_iterations]):
            remaining = deadline - time.monotonic()
            if remaining <= 0: break
            record = await self._one_iteration(i, goal, remaining)
            self.history.append(record)
        return self.history

    async def _state(self):
        tracks = await self.daw.get_tracks()
        return {'project': (await self.daw.get_project()).model_dump(),
                'tracks': [t.model_dump() for t in tracks],
                'clips': [c.model_dump() for c in await self.daw.get_clips()],
                'devices': {t.id: [d.model_dump() for d in await self.daw.get_devices(t.id)] for t in tracks}}

    async def _notes_by_track(self):
        return {t.name: await self.daw.get_notes(t.id) for t in await self.daw.get_tracks()}

    async def _validate(self):
        notes = await self._notes_by_track()
        results = []
        # Legacy callers had an implicit instrumental-only brief. New callers
        # provide Intent and must opt into this naming heuristic explicitly.
        if notes and (self.intent is None or self.intent.constraints.get('no_vocals', False)):
            results.append(check_no_vocals(list(notes)))
        arc_sections = self.plan.sections if self.intent is None or self.intent.constraints.get("enforce_energy_arc", False) else []
        for prev, nxt in zip(arc_sections, arc_sections[1:]):
            if nxt.target_energy >= prev.target_energy:
                results.append(check_energy_relation(notes, self.plan, lhs=nxt.name, rhs=prev.name))
            elif abs(nxt.target_energy-prev.target_energy) > .05:
                results.append(check_energy_relation(notes, self.plan, lhs=prev.name, rhs=nxt.name))
        return results

    async def _measure(self, path):
        from composerbench.analysis.audio import analyze_wav
        from composerbench.analysis.features import section_features
        from composerbench.critique.critic import criticize
        stats = analyze_wav(path)
        info = await self.daw.get_project()
        bounds = self.critic.section_bounds_beats if self.critic and self.critic.section_bounds_beats else self.plan.section_bounds()
        names = [s.name for s in self.plan.sections]
        if len(bounds) != len(names): raise ValueError('one analysis boundary required per section')
        features = section_features(path, bounds, info.tempo_bpm)
        whole = section_features(path, [(0, stats.duration_seconds * info.tempo_bpm / 60)], info.tempo_bpm)[0]
        findings = []
        if self.critic and self.critic.enabled:
            findings = criticize(features, names, self.critic.intent_emotion,
                                 self.critic.energy_targets)
        return {'metrics': {**whole, **stats.to_dict()},
                'sections': dict(zip(names, features)), 'findings': findings}

    def _audio_checks(self, measured):
        peak = measured['metrics']['peak_dbfs']
        checks = []
        if self.render_constraints.max_peak_dbfs is not None:
            checks.append(check_peak_below(peak, self.render_constraints.max_peak_dbfs))
        if self.render_constraints.min_peak_dbfs is not None:
            checks.append(check_audible(peak, self.render_constraints.min_peak_dbfs))
        return checks

    def _persist(self, record):
        directory = self.sidecar.traces if self.sidecar else self.work_dir / 'traces'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{self.run_id}-{record.index}.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(record.to_dict(), indent=2, allow_nan=False))
        tmp.replace(path)

    async def _one_iteration(self, index, goal, remaining=None):
        started = time.monotonic()
        rec = RevisionRecord(index, goal, 'rejected', '', '', None, 0)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        try:
            async with asyncio.timeout(remaining or self.budget.max_seconds):
                async with Transaction(self.daw, self.sidecar, f'rev{index}') as tx:
                    rec.snapshot_id = tx.snapshot_id
                    before_notes = await self._notes_by_track()
                    before_state = await self._state()
                    before = None
                    if self.render_audio and before_state['project']['length_beats'] > 0:
                        path = self.work_dir / f'{self.run_id}-{index}-baseline.wav'
                        rec.baseline_render_path = str(await self.daw.render(RenderRequest(path)))
                        before = await self._measure(path)
                    world = {'goal': goal, 'notes_by_track': before_notes,
                             'state': before_state, 'plan': self.plan.to_dict(),
                             'intent': self.intent.to_dict() if self.intent else {},
                             'capabilities': self.daw.capabilities().to_dict(),
                             'baseline': before, 'objectives': [o.model_dump() for o in self.objectives],
                             'history': self.prior_history + [r.to_dict() for r in self.history]}
                    proposed = self.proposer(world)
                    if inspect.isawaitable(proposed): proposed = await proposed
                    actions = await preflight(self.daw, proposed)
                    rec.actions = [a.model_dump() for a in actions]
                    for action in actions: await _apply(self.daw, action)
                    after = None
                    if self.render_audio:
                        path = self.work_dir / f'{self.run_id}-{index}-candidate.wav'
                        rec.render_path = str(await self.daw.render(RenderRequest(path)))
                        after = await self._measure(path)
                        if self.critic and self.critic.enabled and self.critic.auto_fix:
                            from composerbench.critique.mixer import fixes_for_findings
                            fixes = await preflight(self.daw, fixes_for_findings(after['findings'], self.critic.role_of_track)[:2])
                            if fixes:
                                for action in fixes: await _apply(self.daw, action)
                                rec.actions.extend(a.model_dump() for a in fixes)
                                path = self.work_dir / f'{self.run_id}-{index}-fixed.wav'
                                rec.render_path = str(await self.daw.render(RenderRequest(path)))
                                after = await self._measure(path)
                    # Evaluate ONLY the final candidate, including all corrective edits.
                    validation = await self._validate()
                    if after: validation.extend(self._audio_checks(after))
                    rec.constraint_results = [c.to_dict() for c in validation]
                    rec.diff_summary = diff_tracks(before_notes, await self._notes_by_track()).summary()
                    rec.state_diff = {'before': before_state, 'after': await self._state()}
                    evaluation = {'baseline': before, 'candidate': after, 'improved': False,
                                  'status': 'technical_validation_only'}
                    if before and after:
                        if self.objectives:
                            evaluation.update(compare(before, after, self.objectives))
                            evaluation['status'] = 'measured_objectives'
                        elif self.critic and self.critic.enabled:
                            b = sum(f['severity'] for f in before['findings'])
                            a = sum(f['severity'] for f in after['findings'])
                            evaluation.update(improved=b-a > .01, status='heuristic_critic',
                                              before_penalty=b, after_penalty=a)
                        else:
                            evaluation['status'] = 'needs_goal_objectives'
                    elif after:
                        evaluation['status'] = 'initial_creation'
                    if before and after and self.audition_start_seconds is not None:
                        from composerbench.analysis.audition import compare_audio
                        try:
                            evaluation['audition'] = await asyncio.to_thread(
                                compare_audio, rec.baseline_render_path, rec.render_path,
                                self.work_dir, self.audition_start_seconds,
                                self.audition_duration_seconds)
                        except (ValueError, RuntimeError, OSError) as error:
                            evaluation['audition'] = {'status': 'unavailable', 'reason': str(error)}
                    rec.evaluation = evaluation
                    failed = [c.detail for c in validation if not c.passed]
                    if before and after and not evaluation['improved']:
                        failed.append('no measured improvement toward the goal; supply objectives or review candidate')
                    if self.objectives and not after:
                        failed.append('audio objectives require rendering')
                    if failed:
                        rec.reason = '; '.join(failed)
                    else:
                        rec.decision = 'accepted'
                        rec.reason = ('goal improved and technical checks pass' if evaluation['improved']
                                      else f"technical checks pass ({evaluation['status']}); artistic quality not established")
                        await self.daw.save_project()
                        tx.commit()
        except asyncio.CancelledError:
            rec.decision = 'rejected'
            rec.reason = 'cancelled; transaction restored'
            rec.duration_ms = int((time.monotonic()-started)*1000)
            self._persist(rec)
            raise
        except RollbackError:
            raise  # never claim recovery if recovery itself failed
        except Exception as e:  # noqa: BLE001 - any failed revision is recorded and rejected
            rec.decision = 'rejected'
            rec.reason = f'{type(e).__name__}: {e}'
            rec.constraint_results.append(ConstraintResult('execution', False, rec.reason).to_dict())
        rec.duration_ms = int((time.monotonic()-started)*1000)
        self._persist(rec)
        return rec
