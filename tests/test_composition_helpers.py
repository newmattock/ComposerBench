"""Note-writing helpers, Score and the symbolic checks."""
import pytest

from composerbench.analysis.harmony import check_score, clash_report, density_report, range_report
from composerbench.composition.patterns import (
    arpeggio,
    chord_pitch_classes,
    chord_root,
    hits,
    humanize,
    legato,
    midi,
    note,
    parse_chord,
    progression,
    ramp,
    repeat,
    stutter,
    transpose,
    trim,
    voicing,
)
from composerbench.composition.score import Score


def test_note_names_and_chords():
    assert midi("C4") == 60 and midi("F#2") == 42 and midi("Bb-1") == 10
    assert parse_chord("C#m7") == (1, (0, 3, 7, 10), None)
    assert parse_chord("A/C#") == (9, (0, 4, 7), 1)
    assert chord_pitch_classes("Amaj7") == {9, 1, 4, 8}
    assert chord_root("A/C#", 28) == 37 and chord_root("E", 28) == 28
    with pytest.raises(ValueError):
        parse_chord("Hm")
    with pytest.raises(ValueError):
        parse_chord("Cxyz")


def test_voicings_stay_in_range_and_lead_smoothly():
    v = voicing("Amaj7", 54, 72, 4)
    assert all(54 <= p <= 72 for p in v) and {p % 12 for p in v} == {9, 1, 4, 8}
    prog = progression(["Am", "F", "C", "G"], 55, 76, 4)
    for a, b in zip(prog, prog[1:]):
        assert sum(abs(x - y) for x, y in zip(sorted(a), sorted(b))) <= 12   # no big jumps


def test_drum_strings():
    ns = hits("x.X.o.r.R.3---..", 0.0, 36, velocity=100)
    starts = [n["start"] for n in ns]
    assert starts[:3] == [0.0, 0.5, 1.0]
    assert [n["velocity"] for n in ns[:3]] == [100, 120, 40]
    assert len([s for s in starts if 1.5 <= s < 1.75]) == 2          # 'r' = two 32nds
    assert len([s for s in starts if 2.0 <= s < 2.25]) == 3          # 'R' = three triplets
    assert len([s for s in starts if 2.5 <= s < 3.5]) == 6           # '3' spans 4 steps
    assert hits("x...|x...", 4.0, 36)[1]["start"] == 5.0              # separators ignored
    with pytest.raises(ValueError):
        hits("x?..", 0, 36)


def test_legato_slides_and_retriggers():
    line = [note(0, 1, 40), note(1, 1, 52), note(2, 2, 40)]
    out = legato(line, [True, False, False], overlap=0.06, gap=0.02)
    assert out[0]["start"] + out[0]["duration"] == pytest.approx(1.06)   # glides into the 2nd
    assert out[1]["start"] + out[1]["duration"] == pytest.approx(1.98)   # retriggers the 3rd


def test_legato_does_not_slide_across_a_rest():
    # a slide flagged on the last note of a section must not sustain through the
    # silent section that follows to reach the next note
    line = [note(0, 0.75, 40), note(32, 1, 45)]
    out = legato(line, [True, False])
    assert out[0]["duration"] == pytest.approx(0.75)
    near = legato([note(0, 0.75, 40), note(1.5, 1, 45)], [True, False])
    assert near[0]["start"] + near[0]["duration"] == pytest.approx(1.56)


def test_ramp_trim_and_transforms():
    ns = [note(i, 0.5, 60, 50) for i in range(5)]
    r = ramp(ns, 20, 120)
    assert [n["velocity"] for n in r] == [20, 45, 70, 95, 120]
    t = trim([note(0, 4, 60), note(2.5, 1, 62), note(3.2, 1, 64)], [(3.0, 4.0)])
    assert len(t) == 2 and t[0]["duration"] == pytest.approx(2.98) and t[1]["duration"] == pytest.approx(0.48)
    assert transpose(ns, 12)[0]["pitch"] == 72
    assert len(repeat(ns[:2], 8, 3)) == 6
    assert len(stutter([60, 64], 0, 1, 0.25)) == 8
    h = humanize(ns, 0.02, 5, seed=3)
    assert h == humanize(ns, 0.02, 5, seed=3) and h != ns
    a = arpeggio([60, 64, 67], [0, 1, 2, 3, 4, 5], 0, 0.25)
    assert [n["pitch"] for n in a] == [60, 64, 67, 72, 76, 79]


def test_score_sections_chords_roundtrip(tmp_path):
    sc = Score.from_bars(160, [("intro", 2, "intro"), ("drop", 4, "drop", 2, 0.9)], title="t")
    assert sc.section("drop").start == 8 and sc.section("drop").end == 24 and sc.section("drop").key_shift == 2
    assert sc.section_at(10).name == "drop" and list(sc.bars_of("drop")) == [2, 3, 4, 5]
    sc.chords_from_bars(["Am", ["F", "G"], None, "C"])
    assert [c.symbol for c in sc.chords] == ["Am", "F", "G", "C"]
    assert sc.chord_at(6.5).symbol == "G" and sc.chord_at(9) is None
    sc.add("kick", hits("x...x...x...x...", 0, 36))
    path = sc.save(tmp_path / "s.json")
    back = Score.load(path)
    assert back.to_dict() == sc.to_dict()
    plan = back.to_plan()
    assert [(s.name, s.start_bar, s.bars) for s in plan.sections] == [("intro", 1, 2), ("drop", 3, 4)]
    assert sc.validate() == []
    sc.add("bad", [{"start": 30, "duration": 4, "pitch": 200, "velocity": 0}])
    problems = sc.validate(clip_length=32)
    assert any("pitch 200" in p for p in problems) and any("velocity 0" in p for p in problems)
    assert any("after the clip" in p for p in problems)


def test_clash_and_range_reports():
    sc = Score.from_bars(120, [("a", 2)])
    sc.chords_from_bars(["E", "C"])
    sc.add("lead", [note(0, 1, 77),            # F over E: a half step above the root, strong beat
                    note(1, 1, 75),            # D# over E: major 7th, a tension, fine
                    note(2.25, 0.25, 77),      # F passing tone: short, off-beat, ignored
                    note(4, 2, 76),            # E over C: chord tone
                    note(6, 1, 73)])           # C# over C: b9, clash
    sc.add("kick", hits("x...", 0, 37))        # percussion is never checked
    rep = clash_report(sc)
    assert [c["note"] for c in rep["clashes"]] == ["F5", "C#5"]
    rr = range_report(sc, {"lead": (74, 80)}, mono=["lead"])
    assert len(rr["issues"]) == 1 and rr["issues"][0]["note"] == "C#5"
    assert density_report(sc)["a"]["lead"] == 2.5
    assert check_score(sc)["harmony"]["summary"].startswith("2 clash")
