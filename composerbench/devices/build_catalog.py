"""Build the device catalog (composerbench/devices/catalog.json).

The catalog tells agents (and the song builder's preflight) which MAGDA
instruments and effects exist, what their parameters are called, their
ranges, defaults, units and -- for discrete parameters -- their labels.

Sources, merged in this order:

1. a live probe written by ``examples/hyperpop_probe.py`` (``probe.json``):
   exact names/ranges/defaults as the engine reported them;
2. ``LABELS`` below: discrete-parameter labels read from magda-core's compiled
   plugin sources (``hostSlotInfo_[...].choices`` and the Faust ``menu{}``
   metadata), because the probe predates serve protocol 2's labels;
3. optionally a ``slots.json`` extracted from magda-core sources for effects
   the probe never loaded (marked ``"verified": "source"``).

``refresh_live()`` (``composerbench devices refresh``) re-probes a running engine
over serve protocol 2, whose dumps carry labels, and writes a user catalog to
``~/.musicbench/devices.json`` that takes precedence over the packaged one.

    python -m composerbench.devices.build_catalog probe.json [slots.json] > catalog.json
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

DIVISIONS = ["1/32", "1/16T", "1/16", "1/8T", "1/16.", "1/8", "1/4T", "1/8.",
             "1/4", "1/2T", "1/4.", "1/2", "1/2.", "1/1"]
OFF_ON = ["Off", "On"]

# Discrete-parameter labels (index order == native value). Read from
# magda-core: magda/daw/audio/plugins/compiled/*CompiledPlugin.cpp and
# magda/daw/audio/faust_dsp/compiled/**/*.dsp. Delay/chorus/flanger/mod
# divisions are harvested from Faust and sorted by note length.
LABELS: dict[str, dict[str, list[str]]] = {
    "magda_polysynth": {
        **{f"Osc {i} Wave": ["Sine", "Saw", "Square", "Triangle"] for i in range(1, 5)},
        **{f"Osc {i} Reset": OFF_ON for i in range(1, 5)},
        **{f"Osc {i} Enable": OFF_ON for i in range(1, 5)},
        "Filter Type": ["Lowpass", "Highpass", "Bandpass", "Notch"],
        "Filter Slope": ["12 dB", "24 dB"],
        "Voice Mode": ["Poly", "Mono", "Legato"],
    },
    "magda_fm": {
        **{f"Op{i} Wave": ["Sine", "Triangle", "Saw", "Square", "Noise"] for i in range(1, 5)},
        **{f"Op{i} Reset": OFF_ON for i in range(1, 5)},
        **{f"Op{i} Enable": OFF_ON for i in range(1, 5)},
        "Voice Mode": ["Poly", "Mono", "Legato"],
    },
    "magda_eq": {f"Band {i} Type": ["HP", "LowShelf", "Bell", "HighShelf", "LP", "Notch"]
                 for i in range(1, 9)},
    "magda_reverb": {"Engine": ["Plate", "Hall", "Room"]},
    "magda_delay": {"Division": DIVISIONS, "Sync": OFF_ON},
    "magda_chorus": {"Voices": ["1", "2", "3"], "Sync": OFF_ON, "Division": DIVISIONS},
    "magda_flanger": {"Sync": OFF_ON, "Division": DIVISIONS},
    "magda_compressor": {"Engine": ["Clean", "Glue"], "Detector": ["Peak", "RMS"],
                         "Style": ["Pre", "Post"], "Autogain": OFF_ON},
    "magda_filter": {"Engine": ["SVF", "Ladder", "Korg 35", "Oberheim", "Sallen-Key", "Diode"],
                     "Mode": ["LP", "BP", "HP", "Notch"]},
    "magda_phaser": {"Stages": ["2", "4", "6", "8"]},
    "magda_saturator": {"Mode": ["Tanh", "Soft", "Hard", "Fold", "Tube", "Tape"]},
    "magda_clipper": {"Mode": ["Hard", "Soft", "Tanh", "Hyperbolic", "Sine"]},
    "magda_mod": {"Mode": ["Tremolo", "Vibrato", "Autopan"], "Sync": OFF_ON, "Division": DIVISIONS,
                  "Shape": ["Sine", "Triangle", "Square", "S&H"]},
    "magda_dimension": {"Engine": ["Dimension", "Haas", "M/S"]},
    "magda_pitch": {"Engine": ["Shifter", "Detuner", "Harmonizer"]},
    "magda_grit": {"Mode": ["Noise", "Wide Noise", "Sine"]},
    "magda_ring_mod": {"Sync": OFF_ON, "Division": DIVISIONS, "Shape": ["Sine", "Triangle", "Square"],
                       "Source": ["Oscillator", "Sidechain"]},
    "magda_grain_delay": {"Sync": OFF_ON, "Division": DIVISIONS},
}

NOTES: dict[str, str] = {
    "magda_filter": "Mode labels depend on Engine: SVF/Oberheim LP-BP-HP-Notch, Ladder LP-HP; "
                    "set Engine before Mode.",
    "magda_eq": "8 bands. Enable a band with 'Band N Enabled' = 1 before its Type/Freq/Gain/Q matter.",
    "magda_utility": "Pan/Width/Low Mono live here: the CLI has no track pan, so pan with this.",
    "magda_polysynth": "Voice Mode 'Legato' + Glide (ms) slides between overlapping notes. "
                       "Vel Filter (octaves) lets note velocity open the filter: the only way to sweep "
                       "a filter without automation on protocol 2.",
    "magda_fm": "M<src><dst> is the modulation amount from operator src into operator dst; "
                "Op1 is the carrier by default. Op Wave 'Noise' turns an operator into a noise source.",
    "magda_bell": "Physical bell model; decay in ms.",
    "magda_mod": "Tempo-synced tremolo / vibrato / autopan: Sync On + Division + Shape Square gives a "
                 "rhythmic gate.",
    "magda_multiband": "OTT-style upward/downward multiband compressor (Low/Mid/High). "
                       "Per-band parameters exist but are not catalogued yet: inspect them live.",
    "Surge XT": "Hosted VST3. Parameters are normalised 0..1 and many are not declared discrete. "
                "A Filter 1 Type 0.03 = 'LP 12 dB' (the init patch has the filter Off). "
                "Allow ~4 s after loading before setting parameters.",
}

SUMMARIES: dict[str, str] = {
    "4osc": "Tracktion FourOsc: 4 oscillators with unison detune/spread, filter, envelopes, built-in FX.",
    "magda_polysynth": "4-oscillator subtractive synth: sine/saw/square/triangle, multimode filter with "
                       "envelope and velocity amount, mono/legato glide. Bass, leads, pads, plucks.",
    "magda_fm": "4-operator FM synth with a full modulation matrix. Bells, keys, metallic leads, noise.",
    "magda_kick": "Synth kick: transient + pitched body + click. Short decay = punchy, long = 808 boom.",
    "magda_snare": "Synth snare: transient, tuned body and filtered noise rattle.",
    "magda_clap": "Synth clap: flammed noise bursts with tail.",
    "magda_hat": "Synth hi-hat: metallic ring partials + high-passed noise. Long decays make open hats/crashes.",
    "magda_tom": "Synth tom with pitch bend; short and bent it makes zaps.",
    "magda_bell": "Struck bell model.",
    "magda_marimba": "Struck marimba bar model.",
    "magda_djembe": "Synth djembe.",
    "magda_reverb": "Algorithmic reverb (Plate/Hall/Room) with predelay, damping and cut filters.",
    "magda_delay": "Stereo delay with tempo sync (Division) and ping-pong (Cross).",
    "magda_chorus": "Chorus, 1-3 voices, optional tempo sync.",
    "magda_eq": "8-band parametric EQ (HP/shelves/bell/LP/notch).",
    "magda_compressor": "Compressor (Clean or Glue engine) with sidechain HPF and parallel mix.",
    "magda_filter": "Multimode filter with six engine models and drive.",
    "magda_phaser": "Phaser, 2-8 stages.",
    "magda_flanger": "Flanger with tempo sync.",
    "magda_limiter": "Peak limiter.",
    "magda_bitcrusher": "Sample-rate and bit-depth reduction with drive and tone.",
    "magda_saturator": "Saturation: tanh/soft/hard/fold/tube/tape.",
    "magda_utility": "Gain, pan, width, low-end mono, phase flips.",
    "magda_clipper": "Clipper: hard/soft/tanh/hyperbolic/sine.",
    "magda_mod": "Tremolo / vibrato / autopan with tempo sync.",
    "magda_dimension": "Stereo widener: Dimension, Haas or M/S.",
    "magda_pitch": "Pitch shifter / detuner / harmonizer.",
    "magda_grit": "Adds noise/sine grit around a frequency.",
    "magda_ring_mod": "Ring modulator with tempo sync.",
    "magda_freq_shift": "Frequency shifter.",
    "magda_gate_expander": "Gate / downward expander.",
    "magda_grain_delay": "Granular delay with pitch and spray.",
    "magda_multiband": "OTT-style multiband compressor.",
    "Surge XT": "Hosted Surge XT synth (VST3). Big unison supersaws.",
}

INSTRUMENTS = ["4osc", "magda_polysynth", "magda_fm", "magda_kick", "magda_snare", "magda_clap",
               "magda_hat", "magda_tom", "magda_bell", "magda_marimba", "magda_djembe"]
ALIASES = {"reverb": "magda_reverb", "delay": "magda_delay", "chorus": "magda_chorus",
           "phaser": "magda_phaser", "compressor": "magda_compressor", "4bandEq": "magda_eq",
           "eq": "magda_eq", "equaliser": "magda_eq", "lowpass": "magda_filter",
           "pitchShifter": "magda_pitch", "pitchshift": "magda_pitch"}
SOURCE_FILE = {  # compiled plugin source -> device id, for slots.json entries
    "MagdaDimensionCompiledPlugin.cpp": "magda_dimension", "MagdaFreqShiftCompiledPlugin.cpp": "magda_freq_shift",
    "MagdaGateExpanderCompiledPlugin.cpp": "magda_gate_expander", "MagdaGrainDelayCompiledPlugin.cpp": "magda_grain_delay",
    "MagdaGritCompiledPlugin.cpp": "magda_grit", "MagdaModCompiledPlugin.cpp": "magda_mod",
    "MagdaMultibandCompiledPlugin.cpp": "magda_multiband", "MagdaRingModCompiledPlugin.cpp": "magda_ring_mod",
}
# Surge XT: the subset of normalised parameters ComposerBench has verified by read-back.
SURGE_PARAMS = [
    {"name": "A Filter 1 Type", "min": 0.0, "max": 1.0, "default": 0.005,
     "note": "0.005 Off (init patch), 0.03 LP 12 dB, 0.05 LP 24 dB"},
    {"name": "A Filter 1 Cutoff", "min": 0.0, "max": 1.0, "default": 0.4846},
    {"name": "A Filter 1 Resonance", "min": 0.0, "max": 1.0, "default": 0.0},
    {"name": "A Osc 1 Unison Voices", "min": 0.0, "max": 1.0, "default": 0.005,
     "note": "0.45 ~ 7 voices"},
    {"name": "A Osc 1 Unison Detune", "min": 0.0, "max": 1.0, "default": 0.1},
    {"name": "A Amp EG Attack", "min": 0.0, "max": 1.0, "default": 0.0},
    {"name": "A Amp EG Release", "min": 0.0, "max": 1.0, "default": 0.25},
    {"name": "A Osc 2 Mute", "min": 0.0, "max": 1.0, "default": 1.0},
    {"name": "A Osc 3 Mute", "min": 0.0, "max": 1.0, "default": 1.0},
]


def _param(p: dict, labels: dict[str, list[str]]) -> dict:
    out = {"name": p["name"], "min": _num(p.get("min")), "max": _num(p.get("max")),
           "default": _num(p.get("value", p.get("default")))}
    if p.get("unit"):
        out["unit"] = p["unit"]
    choices = p.get("choices") or labels.get(p["name"])
    if choices:
        out["choices"] = list(choices)
    return out


def _num(v):
    if v is None:
        return None
    v = float(v)
    return round(v, 6) if abs(v) > 1e-5 else 0.0


def _probe_devices(probe: dict) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for group in ("instruments", "fx"):
        for name, entry in probe.get(group, {}).items():
            devs = entry if isinstance(entry, list) else entry.get("devices", [])
            if isinstance(entry, dict) and entry.get("reply", {}).get("ok") is False:
                continue
            match = [d for d in devs if d.get("name") == name] or devs[-1:]
            if match and match[-1].get("parameters"):
                found[name] = match[-1]["parameters"]
    return found


def build(probe_path: Path | str, slots_path: Path | str | None = None) -> dict:
    probe = json.loads(Path(probe_path).read_text())
    live = _probe_devices(probe)
    devices: dict[str, dict] = {}
    for name, params in live.items():
        if name in ALIASES:            # Tracktion-style aliases of magda_* devices
            continue
        labels = LABELS.get(name, {})
        devices[name] = {
            "kind": "instrument" if name in INSTRUMENTS else "effect",
            "format": "internal", "verified": "live",
            "summary": SUMMARIES.get(name, ""),
            "params": [_param(p, labels) for p in params],
        }
    if slots_path:
        slots = json.loads(Path(slots_path).read_text())
        for fname, dev in SOURCE_FILE.items():
            if dev in devices or fname not in slots:
                continue
            labels = LABELS.get(dev, {})
            params = []
            for p in slots[fname]:
                if "name" not in p:
                    continue
                q = {"name": p["name"], "min": p.get("min"), "max": p.get("max"),
                     "value": p.get("default"), "unit": p.get("unit")}
                if q["name"] in labels:
                    q["choices"] = labels[q["name"]]
                    q["min"], q["max"] = 0.0, float(len(labels[q["name"]]) - 1)
                params.append(_param(q, labels))
            devices[dev] = {"kind": "effect", "format": "internal", "verified": "source",
                            "summary": SUMMARIES.get(dev, ""), "params": params}
    if "magda_djembe" not in devices:
        devices["magda_djembe"] = {"kind": "instrument", "format": "internal", "verified": "allowlist",
                                   "summary": SUMMARIES["magda_djembe"], "params": []}
    devices["Surge XT"] = {"kind": "instrument", "format": "VST3", "verified": "live (subset)",
                           "summary": SUMMARIES["Surge XT"], "normalized": True,
                           "params": [dict(p) for p in SURGE_PARAMS]}
    for name, note in NOTES.items():
        if name in devices:
            devices[name]["notes"] = note
    plugins = sorted({p["name"] for p in probe.get("plugins", []) if p.get("format") == "VST3"})
    return {
        "version": 1,
        "generated": date.today().isoformat(),
        "sources": ["live engine probe (examples/hyperpop_probe.py)",
                    "magda-core compiled plugin sources (labels; unprobed effects)"],
        "aliases": dict(ALIASES),
        "hosted_plugins_seen": plugins,
        "devices": dict(sorted(devices.items(), key=lambda kv: (kv[1]["kind"] != "instrument", kv[0]))),
    }


async def refresh_live(cli_path: Path | str, work_dir: Path | str,
                       out_path: Path | str | None = None) -> dict:
    """Probe every catalogued internal device on a running engine and write a
    user catalog (labels straight from the protocol-2 dump)."""
    from composerbench.adapter.magda_serve import MagdaServeAdapter
    from composerbench.devices.catalog import load_catalog, USER_CATALOG

    base = load_catalog(user=False)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    daw = MagdaServeAdapter(cli_path, work / "devices_probe.mgd")
    devices: dict[str, dict] = {}
    errors: dict[str, str] = {}
    await daw.initialize()
    try:
        for name, entry in base["devices"].items():
            if entry.get("format", "internal") != "internal":
                devices[name] = entry
                continue
            try:
                track = await daw.create_track(f"probe_{name}")
                if entry["kind"] == "instrument":
                    await daw.load_device(track.id, name)
                else:
                    await daw.add_fx(track.id, name)
                params = (await daw.get_devices_full(track.id))[-1].get("parameters", [])
                labels = LABELS.get(name, {})
                devices[name] = {**entry, "verified": "live",
                                 "params": [_param(p, labels) for p in params]}
                await daw.delete_track(track.id)
            except Exception as e:  # noqa: BLE001 - record and continue with the rest
                errors[name] = f"{type(e).__name__}: {e}"[:300]
                devices[name] = entry
    finally:
        await daw.shutdown()
    catalog = {**base, "generated": date.today().isoformat(), "devices": devices,
               "sources": base["sources"] + [f"live refresh against {cli_path}"], "refresh_errors": errors}
    target = Path(out_path) if out_path else USER_CATALOG
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(catalog, indent=1))
    return {"written": str(target), "devices": len(devices), "errors": errors}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    json.dump(build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None), sys.stdout, indent=1)
