"""A song's notes, sections and harmony as one JSON-friendly object.

``Score`` is what the song builder plays, the harmony checker reads and the
mix stage uses for sidechain triggers and section reports. Times are
timeline-absolute beats (quarter notes from 0); bars are 0-based here and
converted to the 1-based bars of ``CompositionPlan`` by ``to_plan()``.

    sc = Score.from_bars(160, [("intro", 4), ("verse", 8), ("chorus", 8)])
    sc.chords_from_bars(["C#m", "A", "E", "B"] * 5)        # one chord per bar
    sc.add("kick", hits("x...x...x...x...", sc.beat(4), 36))
    sc.save("score.json")
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from composerbench.composition.patterns import Note, chord_pitch_classes


@dataclass
class Section:
    name: str
    start: float                  # beats
    end: float                    # beats (exclusive)
    kind: str | None = None       # intro/verse/pre/chorus/drop/bridge/outro...
    key_shift: int = 0            # semitones relative to the home key
    energy: float | None = None   # 0..1 target, mirrors CompositionPlan

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Chord:
    start: float
    end: float
    symbol: str

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "symbol": self.symbol}


@dataclass
class Score:
    bpm: float
    beats_per_bar: int = 4
    sections: list[Section] = field(default_factory=list)
    chords: list[Chord] = field(default_factory=list)
    parts: dict[str, list[Note]] = field(default_factory=dict)
    key: str = ""
    title: str = ""
    meta: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- time --
    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm

    def beat(self, bar: float, step: float = 0.0, steps_per_beat: int = 4) -> float:
        """0-based bar (+ optional 16th step) -> beats."""
        return bar * self.beats_per_bar + step / steps_per_beat

    def seconds(self, beats: float) -> float:
        return beats * self.seconds_per_beat

    @property
    def length_beats(self) -> float:
        ends = [s.end for s in self.sections]
        ends += [n["start"] + n["duration"] for ns in self.parts.values() for n in ns]
        return max(ends) if ends else 0.0

    # ------------------------------------------------------------ sections --
    @classmethod
    def from_bars(cls, bpm: float, sections: Iterable, beats_per_bar: int = 4, **kw) -> "Score":
        """Contiguous sections from (name, bars[, kind[, key_shift[, energy]]]) tuples or dicts."""
        sc = cls(bpm=bpm, beats_per_bar=beats_per_bar, **kw)
        t = 0.0
        for s in sections:
            d = dict(s) if isinstance(s, dict) else dict(zip(("name", "bars", "kind", "key_shift", "energy"), s))
            length = float(d.pop("bars")) * beats_per_bar
            sc.sections.append(Section(start=t, end=t + length, **d))
            t += length
        return sc

    def section(self, name: str) -> Section:
        for s in self.sections:
            if s.name == name:
                return s
        raise KeyError(f"no section {name!r}; have {[s.name for s in self.sections]}")

    def section_at(self, beat: float) -> Section | None:
        for s in self.sections:
            if s.start <= beat < s.end:
                return s
        return None

    def bars_of(self, name: str) -> range:
        s = self.section(name)
        return range(int(s.start // self.beats_per_bar), int(s.end // self.beats_per_bar))

    # -------------------------------------------------------------- chords --
    def chords_from_bars(self, symbols: Iterable[str | list[str] | None], start_bar: int = 0) -> "Score":
        """One entry per bar; a list splits that bar evenly; None leaves it empty."""
        for i, sym in enumerate(symbols):
            bar_start = self.beat(start_bar + i)
            parts = sym if isinstance(sym, list) else [sym]
            seg = self.beats_per_bar / len(parts)
            for k, s in enumerate(parts):
                if s:
                    self.chords.append(Chord(bar_start + k * seg, bar_start + (k + 1) * seg, s))
        self.chords.sort(key=lambda c: c.start)
        return self

    def chord_at(self, beat: float) -> Chord | None:
        for c in self.chords:
            if c.start <= beat < c.end:
                return c
        return None

    def chord_tones_at(self, beat: float) -> set[int] | None:
        c = self.chord_at(beat)
        return None if c is None else chord_pitch_classes(c.symbol)

    # --------------------------------------------------------------- parts --
    def add(self, part: str, notes: Iterable[Note]) -> "Score":
        self.parts.setdefault(part, []).extend(dict(n) for n in notes)
        return self

    def sort(self) -> "Score":
        for ns in self.parts.values():
            ns.sort(key=lambda n: (n["start"], n["pitch"]))
        return self

    def validate(self, clip_length: float | None = None) -> list[str]:
        """Problems that would make the engine reject or silently drop notes."""
        problems = []
        limit = clip_length if clip_length is not None else float("inf")
        for part, ns in self.parts.items():
            for i, n in enumerate(ns):
                where = f"{part}[{i}] at beat {n.get('start')}"
                if not 0 <= int(n.get("pitch", -1)) <= 127:
                    problems.append(f"{where}: pitch {n.get('pitch')} outside 0-127")
                if not 1 <= int(n.get("velocity", 0)) <= 127:
                    problems.append(f"{where}: velocity {n.get('velocity')} outside 1-127")
                if n.get("start", -1) < 0 or n.get("duration", 0) <= 0:
                    problems.append(f"{where}: needs start >= 0 and duration > 0")
                elif n["start"] + n["duration"] > limit + 1e-6:
                    problems.append(f"{where}: ends at {n['start'] + n['duration']:.3f}, after the clip "
                                    f"({limit} beats)")
        for a, b in zip(self.sections, self.sections[1:]):
            if b.start < a.end - 1e-9:
                problems.append(f"sections {a.name!r} and {b.name!r} overlap")
        return problems

    def stats(self) -> dict:
        out = {}
        for part, ns in self.parts.items():
            if not ns:
                out[part] = {"notes": 0}
                continue
            pitches = [n["pitch"] for n in ns]
            out[part] = {"notes": len(ns), "low": min(pitches), "high": max(pitches),
                         "first_beat": min(n["start"] for n in ns),
                         "last_beat": max(n["start"] + n["duration"] for n in ns)}
        return out

    # ----------------------------------------------------------------- io --
    def to_dict(self) -> dict:
        return {"title": self.title, "bpm": self.bpm, "beats_per_bar": self.beats_per_bar, "key": self.key,
                "sections": [s.to_dict() for s in self.sections],
                "chords": [c.to_dict() for c in self.chords],
                "parts": self.parts, "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict) -> "Score":
        return cls(bpm=float(d["bpm"]), beats_per_bar=int(d.get("beats_per_bar", 4)),
                   sections=[Section(**s) for s in d.get("sections", [])],
                   chords=[Chord(**c) for c in d.get("chords", [])],
                   parts={k: [dict(n) for n in v] for k, v in d.get("parts", {}).items()},
                   key=d.get("key", ""), title=d.get("title", ""), meta=d.get("meta", {}))

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict()))
        return path

    @classmethod
    def load(cls, path: Path | str) -> "Score":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def to_plan(self):
        """The section layout as a CompositionPlan (1-based bars) for intent/critic tools."""
        from composerbench.composition.state import CompositionPlan, Section as PlanSection
        secs = []
        for s in self.sections:
            bars = max(1, round((s.end - s.start) / self.beats_per_bar))
            secs.append(PlanSection(name=s.name, bars=bars, start_bar=int(s.start // self.beats_per_bar) + 1,
                                    target_energy=s.energy if s.energy is not None else 0.5))
        return CompositionPlan(sections=secs, tempo_bpm=self.bpm, key=self.key or "C major",
                               beats_per_bar=self.beats_per_bar)
