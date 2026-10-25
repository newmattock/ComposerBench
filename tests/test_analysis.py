
from composerbench.analysis.energy import estimate_energy
from composerbench.analysis.symbolic import analyze_notes, slice_notes
from composerbench.model import Note


def _bar_of_quarters(pitch: int = 60, bars: int = 2) -> list[Note]:
    """Steady quarter notes on the beat: maximally unsyncopated."""
    return [Note(start=float(i), duration=1.0, pitch=pitch) for i in range(bars * 4)]


def test_empty_stats():
    stats = analyze_notes([])
    assert stats.note_count == 0
    assert stats.to_dict()["density_per_bar"] == 0.0
    assert estimate_energy(stats) == 0.0


def test_quarter_note_golden():
    notes = _bar_of_quarters(bars=2)
    stats = analyze_notes(notes)
    assert stats.note_count == 8
    assert stats.density_per_bar == 4.0
    assert stats.median_pitch == 60
    assert stats.pitch_range == 0
    assert stats.syncopation == 0.0          # all on integer beats
    assert stats.max_polyphony == 1          # monophonic
    assert stats.silence_ratio == 0.0        # continuous coverage
    d = stats.to_dict()
    assert isinstance(d, dict) and "pitch_class_histogram" in d


def test_offbeat_syncopation_detected():
    steady = analyze_notes(_bar_of_quarters())
    offbeat = analyze_notes([Note(start=i + 0.5, duration=0.5, pitch=60) for i in range(8)])
    assert offbeat.syncopation > steady.syncopation
    assert offbeat.syncopation == 1.0


def test_polyphony_counting():
    chord = [Note(start=0.0, duration=2.0, pitch=p) for p in (60, 64, 67, 72)]
    stats = analyze_notes(chord)
    assert stats.max_polyphony == 4


def test_silence_ratio():
    # One beat of sound in a four-beat span.
    stats = analyze_notes([Note(start=0.0, duration=1.0, pitch=60)], span_beats=4.0)
    assert stats.silence_ratio == 0.75


def test_repetition_ratio_detects_pattern_recurrence():
    motif = [Note(start=0.0, duration=1.0, pitch=60),
             Note(start=1.0, duration=1.0, pitch=64),
             Note(start=2.0, duration=1.0, pitch=67),
             Note(start=3.0, duration=1.0, pitch=60),
             Note(start=4.0, duration=1.0, pitch=64),
             Note(start=5.0, duration=1.0, pitch=67)]
    repeated = analyze_notes(motif)
    randomish = analyze_notes([Note(start=float(i), duration=1.0, pitch=p)
                               for i, p in enumerate([60, 61, 65, 70, 55, 71])])
    assert repeated.repetition_ratio > randomish.repetition_ratio


def test_slice_notes_overlaps_window():
    notes = [Note(start=0.0, duration=4.0, pitch=60),
             Note(start=6.0, duration=1.0, pitch=62)]
    inside = slice_notes(notes, timeline_start=4.0, length_beats=4.0)
    assert len(inside) == 1 and inside[0].pitch == 62


def test_energy_monotone_in_density_and_velocity():
    sparse = analyze_notes([Note(start=float(i * 2), duration=1.0, pitch=60, velocity=40)
                            for i in range(4)], span_beats=8.0)
    dense = analyze_notes([Note(start=i * 0.5, duration=0.5, pitch=76, velocity=120)
                           for i in range(16)], span_beats=8.0)
    e_sparse, e_dense = estimate_energy(sparse), estimate_energy(dense)
    assert 0.0 <= e_sparse < e_dense <= 1.0


def test_energy_matches_spec_arc_example_direction():
    """intro-ish vs climax-ish material must order correctly (spec §31)."""
    intro = analyze_notes([Note(start=float(i * 4), duration=4.0, pitch=60, velocity=50)
                           for i in range(2)], span_beats=8.0)
    climax = analyze_notes([Note(start=i * 0.25, duration=0.5,
                                 pitch=40 + (i % 24), velocity=127)
                            for i in range(32)], span_beats=8.0)
    assert estimate_energy(climax) > estimate_energy(intro)
