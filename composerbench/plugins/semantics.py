"""Semantic plugin profiles (spec §37-38).

Maps raw workstation parameters onto musical concepts so agents can say
"make brighter" while the harness stays explainable: every semantic op
decomposes into explicit parameter changes that are returned, never hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ParameterIntent:
    parameter: str
    direction: int              # +1 increase, -1 decrease
    amount: float               # normalized delta 0..1


@dataclass
class SemanticProfile:
    plugin_id: str
    concepts: dict[str, list[str]] = field(default_factory=dict)
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    def concepts_summary(self) -> list[str]:
        return sorted(self.concepts)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


class SemanticLayer:
    """Resolves concept ops into parameter intents against loaded profiles."""

    def __init__(self, profiles_dir: Path | str | None = None):
        self.profiles: dict[str, SemanticProfile] = {}
        if profiles_dir:
            self.load_profiles(Path(profiles_dir))

    def load_profiles(self, directory: Path) -> None:
        for p in sorted(directory.glob("*.yaml")):
            data = yaml.safe_load(p.read_text()) or {}
            pid = str(data.get("plugin") or p.stem)
            concepts_raw = data.get("concepts", {})
            ranges_raw = data.get("ranges", {})
            self.profiles[pid] = SemanticProfile(
                plugin_id=pid,
                concepts={k: [str(x) for x in v] for k, v in concepts_raw.items()},
                ranges={k: (float(v[0]), float(v[1])) for k, v in ranges_raw.items()},
            )

    def register(self, profile: SemanticProfile) -> None:
        self.profiles[profile.plugin_id] = profile

    def resolve(self, plugin_id: str, concept_op: str) -> list[ParameterIntent]:
        """'increase brightness' -> explicit parameter intents.

        concept_op format: '<direction> <concept>' e.g.
        'increase brightness', 'decrease attack'.
        """
        profile = self.profiles.get(plugin_id)
        if profile is None:
            from composerbench.errors import EntityNotFound
            raise EntityNotFound(
                "semantic-profile", plugin_id, sorted(self.profiles)
            )
        parts = concept_op.split(maxsplit=1)
        if len(parts) != 2 or parts[0] not in ("increase", "decrease"):
            raise ValueError(f"concept op must be '<increase|decrease> <concept>': {concept_op!r}")
        direction = 1 if parts[0] == "increase" else -1
        concept = parts[1].strip().lower()
        params = profile.concepts.get(concept)
        if not params:
            from composerbench.errors import EntityNotFound
            raise EntityNotFound(
                f"concept:{concept}", plugin_id,
                [f"{d} {c}" for c in profile.concepts for d in ("increase", "decrease")],
            )
        return [
            ParameterIntent(parameter=p, direction=direction, amount=self._default_amount(p))
            for p in params
        ]

    def apply(self, plugin_id: str, current_values: dict[str, float],
              intents: list[ParameterIntent]) -> dict[str, tuple[float, float]]:
        """Explainable application: returns {param: (before, after)}."""
        profile = self.profiles.get(plugin_id)
        changes: dict[str, tuple[float, float]] = {}
        for intent in intents:
            before = current_values[intent.parameter]
            lo, hi = profile.ranges.get(intent.parameter, (0.0, 1.0))
            span = (hi - lo) or 1.0
            delta = intent.direction * intent.amount * span
            after = _clamp01(before + delta) if span == 1.0 else max(lo, min(hi, before + delta))
            changes[intent.parameter] = (before, round(after, 6))
        return changes

    @staticmethod
    def _default_amount(param: str) -> float:
        # Gentler on temporal envelope controls than spectral ones;
        # documented heuristic, agents may override via explicit amounts.
        temporal = any(k in param.lower() for k in ("attack", "release", "decay"))
        return 0.12 if temporal else 0.18

    def describe_change(self, plugin_id: str, changes: dict[str, tuple[float, float]]) -> str:
        lines = [f"{plugin_id}:"]
        for param, (before, after) in changes.items():
            pct = ""
            if before > 0:
                pct = f" ({(after - before) / before * 100:+.0f}%)"
            lines.append(f"  {param}: {before:.2f} -> {after:.2f}{pct}")
        return "\n".join(lines)


DEFAULT_FOUR_OSC = SemanticProfile(
    plugin_id="4osc",
    concepts={
        "brightness": ["cutoff"],
        "fullness": ["level"],
        "sharpness": ["attack"],
        "sustain_length": ["release"],
    },
    ranges={
        "cutoff": (80.0, 18000.0),
        "level": (0.0, 1.0),
        "attack": (0.001, 2.0),
        "release": (0.01, 4.0),
    },
)
