"""Explicit goal metrics. A gain-only change cannot win a timbral goal."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Objective(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    metric: Literal['rms_dbfs', 'peak_dbfs', 'crest_factor_db', 'centroid_hz',
                    'low_energy', 'mid_energy', 'high_energy', 'stereo_width',
                    'transient_density']
    direction: Literal['increase', 'decrease', 'target'] = 'target'
    target: float | None = None
    section: str | None = None
    min_improvement: float = Field(default=0.01, gt=0)

    @model_validator(mode='after')
    def require_target(self):
        if self.direction == 'target' and self.target is None:
            raise ValueError('target objective requires target value')
        return self


def compare(before: dict, after: dict, objectives: list[Objective]) -> dict:
    evidence = []
    for objective in objectives:
        o = objective
        b = before['sections'][o.section] if o.section else before['metrics']
        a = after['sections'][o.section] if o.section else after['metrics']
        bv, av = b[o.metric], a[o.metric]
        gain = (abs(bv-o.target)-abs(av-o.target) if o.direction == 'target'
                else (av-bv) * (1 if o.direction == 'increase' else -1))
        evidence.append({**o.model_dump(), 'before': bv, 'after': av,
                         'improvement': gain, 'improved': gain >= o.min_improvement,
                         'regressed': gain <= -o.min_improvement})
    return {'improved': bool(evidence) and any(e['improved'] for e in evidence)
            and not any(e['regressed'] for e in evidence), 'objectives': evidence}
