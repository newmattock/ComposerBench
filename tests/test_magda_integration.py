"""Integration tests for MagdaCliAdapter against the real headless CLI.

Skipped automatically when magda-cli binary isn't built yet — unit suites
must stay green on machines without the workstation build.
"""

import json
import shutil
from pathlib import Path

import pytest

from composerbench.adapter.magda import InvalidNote, MagdaCliAdapter, parse_note_pitch
from composerbench.errors import EntityNotFound
from composerbench.model import Note

MAGDA_CLI = Path(
    "~/magda-core/cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli"
).expanduser()

pytestmark = pytest.mark.skipif(
    not MAGDA_CLI.exists(), reason="magda-cli binary not built yet"
)


@pytest.fixture()
def adapter(tmp_path: Path) -> MagdaCliAdapter:
    import asyncio

    project = tmp_path / "proj.mgd"
    daw = MagdaCliAdapter(MAGDA_CLI, project)
    asyncio.run(daw.initialize())
    return daw


def test_pitch_parsing():
    assert parse_note_pitch(60) == 60
    assert parse_note_pitch("C4") == 60
    assert parse_note_pitch("A4") == 69
    with pytest.raises(InvalidNote):
        parse_note_pitch("H9")


async def test_create_track_and_notes_end_to_end(adapter: MagdaCliAdapter):
    track = await adapter.create_track("Bass")
    clip = await adapter.create_clip(track.id, "verse-bass", start=0.0, length=8.0)
    await adapter.add_notes(track.id, clip.id, [Note(start=0, duration=1, pitch=36)])
    notes = await adapter.get_notes(track.id)
    assert len(notes) == 1 and notes[0].pitch == 36


async def test_snapshot_restore(adapter: MagdaCliAdapter):
    track = await adapter.create_track("Drums")
    snap = await adapter.snapshot("before")
    await adapter.delete_track(track.id)
    tracks_now = await adapter.get_tracks()
    assert all(t.name != "Drums" for t in tracks_now)
    await adapter.restore_snapshot(snap)
    names = [t.name for t in await adapter.get_tracks()]
    assert "Drums" in names


async def test_render_produces_wav(adapter: MagdaCliAdapter, tmp_path: Path):
    from composerbench.adapter.base import RenderRequest

    track = await adapter.create_track("Piano")
    clip = await adapter.create_clip(track.id, "c1", 0.0, 4.0)
    await adapter.add_notes(track.id, clip.id,
                            [Note(start=i, duration=1, pitch=60 + i) for i in range(4)])
    out = tmp_path / "out.wav"
    path = await adapter.render(RenderRequest(output_path=out))
    assert path.exists() and path.stat().st_size > 44
    from composerbench.analysis.audio import analyze_wav
    stats = analyze_wav(path)
    assert stats.duration_seconds > 0.5


from composerbench.adapter.base import RenderRequest as _RR  # noqa: E402


async def test_render_section_range(adapter: MagdaCliAdapter, tmp_path: Path):
    track = await adapter.create_track("Piano")
    clip = await adapter.create_clip(track.id, "c1", 0.0, 8.0)
    await adapter.add_notes(track.id, clip.id,
                            [Note(start=i, duration=1, pitch=60) for i in range(8)])
    out = tmp_path / "section.wav"
    req = _RR(output_path=out, start_beats=0.0, length_beats=4.0)
    path = await adapter.render(req)
    out2 = tmp_path / "whole.wav"
    whole_path = await adapter.render(_RR(output_path=out2))
    from composerbench.analysis.audio import analyze_wav
    assert whole_path.stat().st_size > path.stat().st_size
