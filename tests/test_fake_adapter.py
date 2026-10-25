import json
from pathlib import Path

import pytest

from composerbench.adapter import FakeDawAdapter, RenderRequest
from composerbench.errors import EntityNotFound, ParameterOutOfRange
from composerbench.model import Note


def test_capabilities_discovery():
    cap = FakeDawAdapter().capabilities().to_dict()
    assert cap["midi_edit"] is True
    assert cap["offline_render"] is True


async def test_create_track_and_add_notes():
    daw = FakeDawAdapter(tempo_bpm=90.0)
    track = await daw.create_track("piano")
    assert track.id == "track:piano"

    clip = await daw.create_clip(track.id, "verse-piano", start=0.0, length=16.0)
    n = await daw.add_notes(track.id, clip.id, [Note(start=0, duration=1, pitch=69)])
    assert n == 1

    notes = await daw.get_notes(track.id)
    assert len(notes) == 1 and notes[0].pitch == 69


async def test_structured_error_on_missing_track():
    daw = FakeDawAdapter()
    with pytest.raises(EntityNotFound) as excinfo:
        await daw.get_track("track:bass")
    payload = excinfo.value.to_dict()
    assert payload["error"] == "ENTITY_NOT_FOUND"
    assert json.dumps(payload)  # must be JSON-serializable (spec §21)


async def test_fuzzy_match_suggestion():
    daw = FakeDawAdapter()
    await daw.create_track("Bass Synth")
    with pytest.raises(EntityNotFound) as excinfo:
        await daw.get_track("track:bass")
    assert "Bass Synth" in excinfo.value.details["available_matches"]
    payload = excinfo.value.to_dict()
    assert payload["requested"] == "track:bass"


async def test_parameter_range_is_structured():
    daw = FakeDawAdapter()
    await daw.create_track("pad")
    dev = await daw.load_device("track:pad", "ExampleSynth")
    with pytest.raises(ParameterOutOfRange):
        await daw.set_parameter("track:pad", dev.id, "cutoff", 1.4)
    await daw.set_parameter("track:pad", dev.id, "cutoff", 0.42)
    params = await daw.get_parameters("track:pad", dev.id)
    assert params["cutoff"]["value"] == pytest.approx(0.42)


async def test_render_writes_real_audio_file(tmp_path: Path):
    daw = FakeDawAdapter()
    t = await daw.create_track("piano")
    clip = await daw.create_clip(t.id, "c1", 0.0, 4.0)
    await daw.add_notes(t.id, clip.id, [Note(start=i, duration=0.5, pitch=60 + i) for i in range(4)])
    out = tmp_path / "renders" / "test.wav"
    path = await daw.render(RenderRequest(output_path=out))
    data = path.read_bytes()
    assert path.exists() and len(data) > 1000
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"


async def test_snapshot_restore_reverts_edits():
    daw = FakeDawAdapter()
    await daw.create_track("drums")
    snap = await daw.snapshot("before_edit")
    await daw.delete_track("track:drums")
    tracks_after = await daw.get_tracks()
    assert tracks_after == []
    await daw.restore_snapshot(snap)
    assert [tr.name for tr in await daw.get_tracks()] == ["drums"]


async def test_undo_redo_revisions(monkeypatch=None):
    daw = FakeDawAdapter()
    rev0 = (await daw.get_project()).revision
    await daw.create_track("a")
    rev1 = (await daw.get_project()).revision
    assert rev1 > rev0
    await daw.undo()
    assert (await daw.get_project()).revision > rev1


async def test_modify_notes_transpose_clamps():
    daw = FakeDawAdapter()
    t = await daw.create_track("lead")
    clip = await daw.create_clip(t.id, "melody", 0.0, 4.0)
    await daw.add_notes(t.id, clip.id, [Note(start=0, duration=1, pitch=120)])
    changed = await daw.modify_notes(t.id, clip.id, [0], transpose=-5)
    assert changed == 1
    notes = await daw.get_notes(t.id)
    assert notes[0].pitch == 115


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
