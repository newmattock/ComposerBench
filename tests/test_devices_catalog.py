"""Device catalog lookups, value checks, and that every preset passes them."""
from composerbench.devices import catalog
from composerbench.devices.presets import PRESETS, get_preset, list_presets


def test_catalog_covers_the_engine():
    names = {d["name"] for d in catalog.list_devices()}
    for must in ("magda_polysynth", "magda_fm", "magda_kick", "magda_snare", "magda_clap", "magda_hat",
                 "magda_tom", "magda_bell", "4osc", "magda_reverb", "magda_delay", "magda_eq",
                 "magda_compressor", "magda_filter", "magda_saturator", "magda_bitcrusher", "magda_utility",
                 "magda_mod", "Surge XT"):
        assert must in names
    poly = catalog.device("magda_polysynth")
    wave = catalog.find_param(poly, "Osc 1 Wave")
    assert wave["choices"] == ["Sine", "Saw", "Square", "Triangle"] and wave["max"] == 3
    assert catalog.find_param(catalog.device("delay"), "Division")["choices"][7] == "1/8."
    assert catalog.canonical_name("4bandEq") == "magda_eq" and catalog.canonical_name("REVERB") == "magda_reverb"


def test_check_value_rules():
    ok = catalog.check_value("magda_polysynth", "Voice Mode", "legato")
    assert ok.ok and ok.resolved == 2.0 and ok.severity == "ok"
    assert catalog.check_value("magda_reverb", "Engine", "Hall").resolved == 1.0
    clamp = catalog.check_value("magda_polysynth", "Cutoff", 99999)
    assert clamp.ok and clamp.severity == "warning" and clamp.resolved == 18000
    typo = catalog.check_value("magda_polysynth", "Cuttoff", 1000)
    assert not typo.ok and "Cutoff" in typo.message
    bad_label = catalog.check_value("magda_delay", "Division", "1/7")
    assert not bad_label.ok and "1/8." in bad_label.message
    assert catalog.check_value("magda_polysynth", "Cutoff", "bright").ok is False
    fallback = catalog.check_value("magda_eq", "Band 1 Type", {"label": "nope", "value": 0})
    assert fallback.ok
    unknown_dev = catalog.check_value("someplugin", "x", 1)
    assert unknown_dev.ok and unknown_dev.severity == "warning"
    surge_extra = catalog.check_value("Surge XT", "B Osc 1 Pitch", 0.5)
    assert surge_extra.ok and surge_extra.severity == "info"
    assert "Division" in catalog.describe("magda_delay")


def test_every_preset_is_valid_against_the_catalog():
    problems = []
    for name, p in PRESETS.items():
        inst = p["instrument"]
        dev = inst.get("device") or inst.get("plugin")
        entry = catalog.device(dev)
        assert entry is not None and entry["kind"] == "instrument", name
        for d, params in [(dev, inst["params"])] + [(f["device"], f["params"]) for f in p.get("fx", [])]:
            assert catalog.device(d) is not None, (name, d)
            for k, v in params.items():
                c = catalog.check_value(d, k, v)
                if not c.ok or c.severity == "warning":
                    problems.append((name, d, k, v, c.message))
        assert p["stem"] and 0 < p["volume"] <= 1.0
    assert problems == []
    assert {r["name"] for r in list_presets()} == set(PRESETS)
    preset = get_preset("bass_808")
    preset["instrument"]["params"]["Glide"] = 1          # copies, never the shared table
    assert PRESETS["bass_808"]["instrument"]["params"]["Glide"] == 55.0


def test_every_internal_device_is_live_verified():
    # djembe used to have no parameters and multiband / grain delay came from
    # source reading; the shipped catalog is now a live probe of every device
    from composerbench.devices.catalog import load_catalog
    devices = load_catalog(user=False)["devices"]
    internal = {n: d for n, d in devices.items() if d.get("format", "internal") == "internal"}
    assert all(d["verified"] == "live" and d["params"] for d in internal.values())
    division = next(p for p in internal["magda_grain_delay"]["params"] if p["name"] == "Division")
    assert division["choices"][0] == "1/16T" and division["max"] == 11.0
    assert any(p["name"] == "High Attack" for p in internal["magda_multiband"]["params"])
