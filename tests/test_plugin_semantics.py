import pytest

from composerbench.plugins.semantics import (
    DEFAULT_FOUR_OSC,
    ParameterIntent,
    SemanticLayer,
    SemanticProfile,
)


@pytest.fixture()
def layer():
    layer = SemanticLayer()
    layer.register(DEFAULT_FOUR_OSC)
    return layer


def test_resolve_brightness_to_explicit_parameters(layer):
    intents = layer.resolve("4osc", "increase brightness")
    assert [i.parameter for i in intents] == ["cutoff"]
    assert intents[0].direction == 1


def test_unknown_concept_lists_available(layer):
    with pytest.raises(Exception) as e:
        layer.resolve("4osc", "increase warmth")
    assert "brightness" in str(e.value) or "available" in str(e.value).lower() \
        or hasattr(e.value, "details")


def test_apply_is_explainable_and_bounded(layer):
    intents = [ParameterIntent(parameter="cutoff", direction=1, amount=0.18)]
    changes = layer.apply("4osc", {"cutoff": 800.0}, intents)
    before, after = changes["cutoff"]
    assert before == 800.0
    # 18% of the (80..18000) span added
    expected = min(18000.0, 800.0 + 0.18 * (18000.0 - 80.0))
    assert after == pytest.approx(round(expected, 6))
    text = layer.describe_change("4osc", changes)
    assert "cutoff" in text and "->" in text


def test_temporal_params_move_gently(layer):
    sharp = layer.resolve("4osc", "decrease sharpness")[0]
    gentle = layer.resolve("4osc", "increase brightness")[0]
    assert sharp.parameter == "attack"
    assert abs(sharp.amount) < abs(gentle.amount)


def test_yaml_profile_loading(tmp_path):
    import yaml

    d = tmp_path / "profiles"
    d.mkdir()
    (d / "my synth.yaml").write_text(yaml.safe_dump({
        "plugin": "my-synth",
        "concepts": {"width": ["stereo"], "mud": ["low_mid"]},
        "ranges": {"stereo": [0.0, 1.0], "low_mid": [-12.0, 12.0]},
    }))
    layer = SemanticLayer(d)
    intents = layer.resolve("my-synth", "increase width")
    assert intents[0].parameter == "stereo"
