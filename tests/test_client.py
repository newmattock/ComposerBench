import pytest

from composerbench.client import ComposerBench
from composerbench.errors import EntityNotFound
from composerbench.model import Note


def test_connect_fake_defaults():
    mb = ComposerBench.connect_fake()
    assert mb.capabilities()["midi_edit"] is True


async def test_milestone2_flow(tmp_path):
    mb = ComposerBench.connect_fake(tmp_path)
    await mb.create_track("piano")
    await mb.create_clip("piano", "verse", start=0.0, length=16.0)
    added = await mb.add_notes("piano", clip="verse",
                               notes=[Note(start=i, duration=1, pitch=60 + i) for i in range(4)])
    assert added == 4
    tracks = await mb.tracks()
    assert [t.name for t in tracks] == ["piano"]
    notes = await mb.notes("piano")
    assert len(notes) == 4


async def test_add_notes_without_clip_fails_actionably(tmp_path):
    mb = ComposerBench.connect_fake(tmp_path)
    await mb.create_track("empty")
    with pytest.raises(LookupError, match="no clip"):
        await mb.add_notes("empty", notes=[Note(start=0, duration=1, pitch=60)])


async def test_intent_and_plan_roundtrip_via_client(tmp_path):
    from composerbench.composition.state import CompositionPlan, Intent, Section

    mb = ComposerBench.connect_fake(tmp_path)
    await mb.write_intent(Intent(title="demo", goal="become enormous"))
    await mb.write_plan(CompositionPlan(sections=[Section(name="intro", bars=8, start_bar=1)]))
    intent = await mb.read_intent()
    plan = await mb.read_plan()
    assert intent.goal == "become enormous"
    assert plan.sections[0].name == "intro"


async def test_unknown_track_raises_structured():
    mb = ComposerBench.connect_fake()
    with pytest.raises(EntityNotFound):
        await mb.track("track:nope")
