"""ComposerBench sidecar state (spec §12–14).

MAGDA owns audio-production state; this module owns artistic intent,
the composition plan, and semantic section labels stored under
`<project>/.musicbench/`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import math
import yaml

COMPOSERBENCH_DIR = ".musicbench"


@dataclass
class Section:
    name: str
    bars: int
    start_bar: int          # 1-based, matching spec examples ("intro 1–8")
    target_energy: float = 0.5
    instruments: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.name or self.bars < 1 or self.start_bar < 1:
            raise ValueError('sections require a name, positive bars and one-based start_bar')
        if not math.isfinite(self.target_energy) or not 0 <= self.target_energy <= 1:
            raise ValueError('target_energy must be between zero and one')

    @property
    def end_bar(self) -> int:  # inclusive
        return self.start_bar + self.bars - 1


@dataclass
class CompositionPlan:
    """Section-level implementation of the artistic intent."""

    sections: list[Section] = field(default_factory=list)
    tempo_bpm: float = 120.0
    key: str = "C major"
    beats_per_bar: int = 4

    def __post_init__(self):
        if not math.isfinite(self.tempo_bpm) or self.tempo_bpm <= 0 or self.beats_per_bar <= 0:
            raise ValueError('tempo and beats_per_bar must be positive')
        if len({s.name for s in self.sections}) != len(self.sections):
            raise ValueError('section names must be unique')
        for a, b in zip(self.sections, self.sections[1:]):
            if a.end_bar >= b.start_bar:
                raise ValueError('sections must be ordered and nonoverlapping')

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.name == name), None)

    def bar_range_to_beats(self, start_bar: int, end_bar: int) -> tuple[float, float]:
        """1-based inclusive bar range -> (timeline_start_beats, length_beats)."""
        if start_bar < 1 or end_bar < start_bar:
            raise ValueError("bar range must be one-based and increasing")
        start = (start_bar - 1) * self.beats_per_bar
        return float(start), float((end_bar - start_bar + 1) * self.beats_per_bar)

    def section_bounds(self) -> list[tuple[float, float]]:
        return [(start, start + length) for start, length in
                (self.bar_range_to_beats(s.start_bar, s.end_bar) for s in self.sections)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tempo_bpm": self.tempo_bpm,
            "key": self.key,
            "beats_per_bar": self.beats_per_bar,
            "sections": [asdict(s) for s in self.sections],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompositionPlan:
        sections = [
            Section(
                name=s["name"],
                bars=int(s["bars"]),
                start_bar=int(s["start_bar"]),
                target_energy=float(s.get("target_energy", 0.5)),
                instruments=list(s.get("instruments", [])),
            )
            for s in data.get("sections", [])
        ]
        return cls(
            sections=sections,
            tempo_bpm=float(data.get("tempo_bpm", 120.0)),
            key=str(data.get("key", "C major")),
            beats_per_bar=int(data.get("beats_per_bar", 4)),
        )


@dataclass
class Intent:
    """Structured artistic intent (spec §13). Free-form by design; validated lazily."""

    title: str = "untitled"
    goal: str = ""
    emotion: dict[str, list[str]] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    principles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "goal": self.goal,
            "emotion": self.emotion,
            "constraints": self.constraints,
            "principles": self.principles,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Intent:
        return cls(
            title=str(data.get("title", "untitled")),
            goal=str(data.get("goal", "")),
            emotion={k: list(v) for k, v in data.get("emotion", {}).items()},
            constraints=dict(data.get("constraints", {})),
            principles=[str(p) for p in data.get("principles", [])],
        )


def default_two_section_plan() -> CompositionPlan:
    return CompositionPlan(
        sections=[
            Section(name="intro", bars=8, start_bar=1, target_energy=0.15),
            Section(name="climax", bars=8, start_bar=9, target_energy=0.9),
        ],
    )


class SidecarStore:
    """Reads/writes `.musicbench/` state next to a DAW project."""

    def __init__(self, project_dir: Path | str):
        self.root = Path(project_dir) / COMPOSERBENCH_DIR
        self.evaluations = self.root / "evaluations"
        self.renders = self.root / "renders"
        self.snapshots = self.root / "snapshots"
        self.traces = self.root / "traces"
        self.memory_dir = self.root / "memory"

    def ensure_layout(self) -> None:
        for d in (self.root, self.evaluations, self.renders, self.snapshots, self.traces, self.memory_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- intent ---------------------------------------------------------------
    def read_intent(self) -> Intent:
        path = self.root / "intent.yaml"
        if not path.exists():
            return Intent()
        return Intent.from_dict(yaml.safe_load(path.read_text()) or {})

    def write_intent(self, intent: Intent) -> Path:
        self.ensure_layout()
        path = self.root / "intent.yaml"
        path.write_text(yaml.safe_dump(intent.to_dict(), sort_keys=False))
        return path

    # ---- plan -----------------------------------------------------------------
    def read_plan(self) -> CompositionPlan:
        path = self.root / "plan.yaml"
        if not path.exists():
            return default_two_section_plan()
        return CompositionPlan.from_dict(yaml.safe_load(path.read_text()) or {})

    def write_plan(self, plan: CompositionPlan) -> Path:
        self.ensure_layout()
        path = self.root / "plan.yaml"
        path.write_text(yaml.safe_dump(plan.to_dict(), sort_keys=False))
        return path
