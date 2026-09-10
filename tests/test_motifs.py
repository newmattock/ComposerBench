from pathlib import Path

import pytest

from composerbench.composition.motifs import (
    MotifStore,
    augment,
    find_occurrences,
    invert,
    transpose,
)
from composerbench.model import Note


def motif_notes(start: float = 0.0, pitch: int = 60) -> list[Note]:
    # E-G-A contour (+4,+3 intervals), the spec's "tiny melody".
    return [Note(start=start + i, duration=0.9, pitch=pitch + d, velocity=90)
            for i, d in enumerate([0, 4, 7])]


def test_transposition_invariant_search():
    motif = motif_notes()
    candidates = [
        *motif_notes(8.0, pitch=72),          # same intervals, +12
        Note(start=20.0, duration=1.0, pitch=70),
        Note(start=21.0, duration=1.0, pitch=71),
        Note(start=22.0, duration=1.0, pitch=69),
    ]
    hits = find_occurrences(motif, candidates)
    pitches = [(h.start, h.pitch) for h in hits]
    assert (8.0, 72) in pitches and (10.0, 79) in pitches   # transposed restatement found


def test_no_false_positive_on_different_contour():
    motif = motif_notes()
    other = [Note(start=i, duration=1.0, pitch=p)
             for i, p in [(0, 60), (1, 61), (2, 62)]]
    assert find_occurrences(motif, other) == []


def test_transformations_preserve_contour_class():
    motif = motif_notes()
    up = transpose(motif, 5)
    assert [n.pitch for n in up] == [65, 69, 72]
    inv = invert(motif)
    assert [n.pitch for n in inv] == [60, 56, 53]
    aug = augment(motif, 2.0)
    assert aug[1].start == pytest_approx(motif[1].start * 2 - motif[0].start)
    assert aug[0].duration == pytest.approx(1.8)


def pytest_approx(x):
    return pytest.approx(x)


def test_store_lineage_roundtrip(tmp_path: Path):
    store = MotifStore(tmp_path)
    motif = motif_notes()
    app = store.record("motif:main-theme", "piano", motif)
    assert app.appearance_id == "motif:main-theme#0"
    t = transpose(motif, -3)
    store.record("motif:main-theme", "synth", t, transformation="transpose", semitones=-3)

    reloaded = MotifStore(tmp_path)
    lines = reloaded.lineage("motif:main-theme")
    assert len(lines) == 2
    assert "original" in lines[0] and "transpose (-3st)" in lines[1]
    assert reloaded.appearances["motif:main-theme#1"].notes[0].pitch == 57
