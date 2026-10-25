"""Parameter meaning: labels in, labels out (composerbench.params)."""
import pytest

from composerbench.params import (
    ParameterNotFound,
    ParameterValueInvalid,
    UnknownParameterLabel,
    describe,
    find,
    label_for,
    resolve_value,
)

ENGINE = {"name": "Engine", "value": 0.0, "min": 0.0, "max": 2.0, "choices": ["Plate", "Hall", "Room"]}
DIVISION = {"name": "Division", "value": 7.0, "min": 0.0, "max": 13.0,
            "choices": ["1/32", "1/16T", "1/16", "1/8T", "1/16.", "1/8", "1/4T", "1/8.",
                        "1/4", "1/2T", "1/4.", "1/2", "1/2.", "1/1"]}
# Hosted plugins expose discrete parameters as normalised 0..1 values.
FILTER_TYPE = {"name": "A Filter 1 Type", "value": 0.005, "min": 0.0, "max": 1.0,
               "choices": ["Off", "LP 12 dB", "LP 24 dB", "HP 12 dB"]}
CUTOFF = {"name": "Cutoff", "value": 3000.0, "min": 50.0, "max": 18000.0, "unit": "Hz"}


def test_internal_discrete_labels_map_to_indices():
    assert resolve_value(ENGINE, "Hall") == 1.0
    assert resolve_value(ENGINE, "  room ") == 2.0
    assert resolve_value(DIVISION, "1/8.") == 7.0
    assert label_for(DIVISION) == "1/8."


def test_normalised_discrete_labels_for_hosted_plugins():
    assert resolve_value(FILTER_TYPE, "LP 24 dB") == pytest.approx(2 / 3)
    assert label_for(FILTER_TYPE) == "Off"          # 0.005 is the first state
    assert label_for(FILTER_TYPE, 0.34) == "LP 12 dB"


def test_unknown_label_lists_the_choices():
    with pytest.raises(UnknownParameterLabel) as info:
        resolve_value(ENGINE, "Cathedral")
    assert info.value.to_dict()["choices"] == ["Plate", "Hall", "Room"]
    assert isinstance(info.value, ValueError)


def test_continuous_parameters_reject_words_and_out_of_range_numbers():
    with pytest.raises(UnknownParameterLabel) as info:
        resolve_value(CUTOFF, "bright")
    assert info.value.to_dict()["range"] == [50.0, 18000.0]
    with pytest.raises(ParameterValueInvalid):
        resolve_value(CUTOFF, 20000)
    assert resolve_value(CUTOFF, "5200") == 5200.0


def test_describe_and_find():
    assert describe({**ENGINE, "value": 1.0})["text"] == "Hall"
    key, meta = find({0: {"name": "Mix"}, 1: {"name": "Decay"}}, "decay")
    assert (key, meta["name"]) == (1, "Decay")
    with pytest.raises(ParameterNotFound):
        find({0: {"name": "Mix"}, 1: {"name": "Mix"}}, "Mix")
    with pytest.raises(LookupError):          # still a LookupError for older callers
        find({0: {"name": "Mix"}}, "Width")


def test_engine_reported_choice_positions_win():
    # Surge encodes state i of n at 0.005 + 0.99 * i / (n - 1)
    meta = {"name": "A Filter 1 Type", "value": 0.005, "min": 0.0, "max": 1.0,
            "choices": ["Off", "LP 12 dB", "LP 24 dB"], "choiceValues": [0.005, 0.5, 0.995]}
    assert resolve_value(meta, "LP 24 dB") == 0.995
    assert label_for(meta, 0.52) == "LP 12 dB"
    assert label_for(meta) == "Off"


def test_host_wrapper_parameters_are_marked_and_deprioritised():
    from composerbench.params import mark_host_wrapper, match
    params = dict(enumerate(mark_host_wrapper([
        {"name": "Dry Level", "value": 0.0}, {"name": "Wet Level", "value": 1.0},
        {"name": "Dry Level", "value": 0.8}, {"name": "Late Level", "value": 0.2},
        {"name": "A Filter 1 Cutoff", "value": 0.4}])))
    assert params[1]["stableId"] == "host:Wet Level" and "owner" not in params[2]
    assert find(params, "Dry Level")[0] == 2               # the plugin's own
    assert find(params, "host:Dry Level")[0] == 0          # the wrapper, explicitly
    assert match(params, ["wet level", "wet", "mix"])[0] == 1   # only the wrapper has one
    assert match(params, ["Wet Level"], owner="plugin") is None
    assert match(params, ["Cutoff", "Filter 1 Cutoff"])[0] == 4
    assert match(params, ["late level", "level"])[0] == 3       # exact beats substring
    assert match(params, ["Resonance"]) is None
