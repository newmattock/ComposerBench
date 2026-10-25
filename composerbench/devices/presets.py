"""Track presets: an instrument, its settings, an FX chain, a fader level and a stem.

The values come from GLITTER RUSH (examples/glitter_rush.py), whose 15 tracks
read back from the engine with zero mismatches, and from the device catalog.
Discrete settings are written as ``{"label": ..., "value": ...}`` so they read
well and still work on engines that cannot resolve labels.

Each preset also has a ``level`` (LU relative to the kick) and its measured
``lufs_at_unity`` (devices/levels.py, devices/preset_levels.json): a song spec
that gives no ``volume`` gets the fader that puts the part at its level. The
stored ``volume`` is the fallback for presets that are not calibrated. Use a preset in a song spec with
``{"name": "kick", "preset": "kick_punch"}`` and override anything:
``"params"`` merges into the instrument settings, ``"fx"`` replaces the chain,
``"fx_params"`` merges into chain entries by index, ``"volume"``/``"stem"``
replace the defaults.
"""
from __future__ import annotations

import copy


def L(label: str, value: float) -> dict:
    return {"label": label, "value": value}


SINE, SAW, SQUARE, TRIANGLE = L("Sine", 0), L("Saw", 1), L("Square", 2), L("Triangle", 3)
LEGATO, POLY = L("Legato", 2), L("Poly", 0)
LOWPASS, HIGHPASS = L("Lowpass", 0), L("Highpass", 1)
SLOPE24 = L("24 dB", 1)
OSC34_OFF = {"Osc 3 Level": -60.0, "Osc 4 Level": -60.0, "Osc 3 Enable": L("Off", 0), "Osc 4 Enable": L("Off", 0)}
PLATE, HALL, ROOM = L("Plate", 0), L("Hall", 1), L("Room", 2)
DOTTED_8TH, EIGHTH, SIXTEENTH = L("1/8.", 7), L("1/8", 5), L("1/16", 2)
SYNC_ON = L("On", 1)


def fx(device: str, **params) -> dict:
    return {"device": device, "params": params}


def eq_lowcut(freq: float, band: int = 1) -> dict:
    return {f"Band {band} Enabled": 1.0, f"Band {band} Type": L("HP", 0),
            f"Band {band} Freq": freq, f"Band {band} Q": 0.7}


PRESETS: dict[str, dict] = {
    # ------------------------------------------------------------------ drums
    "kick_punch": {
        "description": "Tight hyperpop/trap kick: fast pitch drop, click, short body.",
        "stem": "drums", "volume": 0.45, "role": "kick",
        "instrument": {"device": "magda_kick", "params": {
            "transient_level": 0.8, "transient_pitch": 300.0, "transient_sweep": 0.7, "transient_decay": 10.0,
            "body_pitch": 46.0, "body_snap": 0.75, "body_snap_time": 40.0, "body_decay": 300.0,
            "body_drive": 5.0, "click_level": 0.45, "click_tone": 5000.0, "Gain": -3.0}},
        "fx": [],
    },
    "kick_boom": {
        "description": "Long, low kick that doubles as an 808 hit; pair with a short bass.",
        "stem": "drums", "volume": 0.42, "role": "kick",
        "instrument": {"device": "magda_kick", "params": {
            "transient_level": 0.6, "transient_pitch": 220.0, "transient_sweep": 0.6, "transient_decay": 12.0,
            "body_pitch": 42.0, "body_snap": 0.6, "body_snap_time": 60.0, "body_decay": 1100.0,
            "body_drive": 4.0, "click_level": 0.3, "click_tone": 3500.0, "Gain": -4.0}},
        "fx": [],
    },
    "snare_tight": {
        "description": "Bright snappy snare with a short plate.",
        "stem": "drums", "volume": 0.35, "role": "snare",
        "instrument": {"device": "magda_snare", "params": {
            "transient_level": 0.8, "transient_pitch": 600.0, "transient_tone": 6000.0, "body_tune": 210.0,
            "body_snap": 0.5, "body_decay": 140.0, "rattle_snappy": 0.8, "rattle_tone": 5200.0,
            "rattle_hp_freq": 700.0, "rattle_decay": 230.0, "rattle_drive": 4.0, "Gain": -6.0}},
        "fx": [fx("magda_reverb", Engine=ROOM, Mix=0.14, Decay=35.0, **{"Low Cut": 250.0})],
    },
    "clap_wide": {
        "description": "Layering clap with a plate tail.",
        "stem": "drums", "volume": 0.375, "role": "clap",
        "instrument": {"device": "magda_clap", "params": {
            "flam_tone": 1500.0, "flam_spread": 12.0, "flam_decay": 280.0, "flam_tail": 0.6,
            "shape_hp_freq": 1200.0, "shape_drive": 4.0, "Gain": -6.0}},
        "fx": [fx("magda_reverb", Engine=PLATE, Mix=0.2, Decay=40.0, **{"Low Cut": 400.0})],
    },
    "hat_closed": {
        "description": "Closed hat, slightly right. Program rolls with 1/32 and 1/16-triplet notes.",
        "stem": "drums", "volume": 0.3, "role": "hat",
        "instrument": {"device": "magda_hat", "params": {
            "ring_level": 0.4, "ring_pitch": 800.0, "ring_spread": 1.4, "ring_decay": 12.0, "noise_level": 0.7,
            "noise_hp_freq": 9500.0, "noise_decay": 45.0, "noise_sat": 0.3, "Gain": -9.0}},
        "fx": [fx("magda_utility", Pan=0.22)],
    },
    "hat_open": {
        "description": "Open hat, slightly left.",
        "stem": "drums", "volume": 0.275, "role": "hat",
        "instrument": {"device": "magda_hat", "params": {
            "ring_level": 0.35, "ring_pitch": 700.0, "ring_spread": 1.5, "ring_decay": 80.0, "noise_level": 0.7,
            "noise_hp_freq": 8000.0, "noise_decay": 380.0, "noise_sat": 0.2, "Gain": -12.0}},
        "fx": [fx("magda_utility", Pan=-0.25)],
    },
    "crash_noise": {
        "description": "Synthetic crash: long hat noise into a hall. One note per downbeat accent.",
        "stem": "fx", "volume": 0.28, "role": "crash",
        "instrument": {"device": "magda_hat", "params": {
            "ring_level": 0.3, "ring_pitch": 560.0, "ring_spread": 1.8, "ring_decay": 900.0, "noise_level": 0.8,
            "noise_hp_freq": 4500.0, "noise_decay": 1400.0, "noise_sat": 0.2, "Gain": -10.0}},
        "fx": [fx("magda_reverb", Engine=HALL, Mix=0.25, Decay=60.0, **{"Low Cut": 500.0}),
               fx("magda_utility", Width=150.0)],
    },
    "impact_boom": {
        "description": "Sub drop / impact for section starts.",
        "stem": "fx", "volume": 0.4, "role": "impact",
        "instrument": {"device": "magda_kick", "params": {
            "transient_level": 0.3, "transient_pitch": 150.0, "transient_sweep": 0.9, "transient_decay": 30.0,
            "body_pitch": 32.0, "body_snap": 0.9, "body_snap_time": 400.0, "body_decay": 2500.0,
            "body_drive": 3.0, "click_level": 0.1, "Gain": -6.0}},
        "fx": [fx("magda_reverb", Engine=HALL, Mix=0.3, Decay=70.0, **{"Low Cut": 120.0})],
    },
    "zap_crushed": {
        "description": "Bitcrushed tom zap with a synced echo.",
        "stem": "fx", "volume": 0.25, "role": "zap",
        "instrument": {"device": "magda_tom", "params": {
            "body_tune": 380.0, "body_bend": 1.0, "body_decay": 140.0, "noise_level": 0.1,
            "noise_tone": 6000.0, "noise_decay": 30.0, "Gain": -10.0}},
        "fx": [fx("magda_bitcrusher", Bits=6.0, Rate=12000.0, Mix=0.5),
               fx("magda_delay", Sync=SYNC_ON, Division=SIXTEENTH, Feedback=0.4, Mix=0.3, Cross=0.8)],
    },
    # ------------------------------------------------------------------- bass
    "bass_808": {
        "description": "Saturated 808-style bass: sine + octave triangle, legato glide. Overlap a note "
                       "into the next to slide (composition.patterns.legato).",
        "stem": "bass", "volume": 0.4, "role": "bass", "range": [24, 52], "mono": True,
        "instrument": {"device": "magda_polysynth", "params": {
            "Osc 1 Wave": SINE, "Osc 1 Level": 0.0, "Osc 2 Wave": TRIANGLE, "Osc 2 Level": -14.0,
            "Osc 2 Coarse": 12.0, **OSC34_OFF, "Filter Type": LOWPASS, "Cutoff": 900.0, "Resonance": 0.1,
            "Filter Drive": 0.55, "Filter Slope": SLOPE24, "Amp Attack": 2.0, "Amp Decay": 1200.0,
            "Amp Sustain": 0.6, "Amp Release": 90.0, "Voice Mode": LEGATO, "Glide": 55.0, "Vel Amp": 0.4,
            "Output": -6.0}},
        "fx": [fx("magda_saturator", Mode=L("Tube", 4), Drive=9.0, Mix=0.8, Output=-3.0),
               fx("magda_eq", **eq_lowcut(28.0))],
    },
    "sub_sine": {
        "description": "Clean sine sub for breakdowns and under supersaws.",
        "stem": "bass", "volume": 0.38, "role": "bass", "range": [24, 50], "mono": True,
        "instrument": {"device": "magda_polysynth", "params": {
            "Osc 1 Wave": SINE, "Osc 1 Level": 0.0, "Osc 2 Level": -60.0, "Osc 2 Enable": L("Off", 0),
            **OSC34_OFF, "Filter Type": LOWPASS, "Cutoff": 400.0, "Resonance": 0.0, "Amp Attack": 4.0,
            "Amp Decay": 800.0, "Amp Sustain": 0.9, "Amp Release": 120.0, "Voice Mode": LEGATO,
            "Glide": 40.0, "Vel Amp": 0.3, "Output": -6.0}},
        "fx": [fx("magda_eq", **eq_lowcut(25.0))],
    },
    # ---------------------------------------------------------------- synths
    "supersaw_surge": {
        "description": "Wide 7-voice Surge XT supersaw for choruses and drops (needs the Surge XT VST3).",
        "stem": "synths", "volume": 0.15, "role": "chords",
        "instrument": {"plugin": "Surge XT", "init_wait_seconds": 4.0, "params": {
            "A Filter 1 Type": 0.03, "A Filter 1 Cutoff": 0.80, "A Filter 1 Resonance": 0.15,
            "A Osc 1 Unison Voices": 0.45, "A Osc 1 Unison Detune": 0.25, "A Amp EG Release": 0.42}},
        "fx": [fx("magda_eq", **eq_lowcut(170.0), **{"Band 7 Enabled": 1.0, "Band 7 Type": L("HighShelf", 3),
                                                    "Band 7 Freq": 9000.0, "Band 7 Gain": 2.0}),
               fx("magda_reverb", Engine=HALL, Mix=0.16, Decay=55.0, **{"Low Cut": 300.0, "High Cut": 10000.0})],
    },
    "supersaw_poly": {
        "description": "Supersaw from three detuned polysynth saws + chorus; no plugin needed.",
        "stem": "synths", "volume": 0.22, "role": "chords",
        "instrument": {"device": "magda_polysynth", "params": {
            "Osc 1 Wave": SAW, "Osc 1 Level": -10.0, "Osc 1 Fine": -14.0,
            "Osc 2 Wave": SAW, "Osc 2 Level": -10.0, "Osc 2 Fine": 14.0,
            "Osc 3 Wave": SAW, "Osc 3 Level": -12.0, "Osc 3 Coarse": 12.0, "Osc 3 Fine": 6.0,
            "Osc 4 Wave": SAW, "Osc 4 Level": -14.0, "Osc 4 Fine": -7.0,
            "Filter Type": LOWPASS, "Cutoff": 7000.0, "Resonance": 0.1, "Amp Attack": 4.0,
            "Amp Decay": 400.0, "Amp Sustain": 0.85, "Amp Release": 220.0, "Vel Amp": 0.3, "Output": -12.0}},
        "fx": [fx("magda_chorus", Voices=L("3", 2), Rate=0.6, Depth=0.45, Mix=0.5, Width=1.0),
               fx("magda_eq", **eq_lowcut(160.0)),
               fx("magda_reverb", Engine=HALL, Mix=0.18, Decay=55.0, **{"Low Cut": 300.0, "High Cut": 11000.0})],
    },
    "pad_sweep": {
        "description": "Warm saw pad whose filter follows note velocity (Vel Filter): ramp velocities "
                       "to sweep it open (composition.patterns.ramp).",
        "stem": "synths", "volume": 0.275, "role": "pad",
        "instrument": {"device": "magda_polysynth", "params": {
            "Osc 1 Wave": SAW, "Osc 1 Level": -10.0, "Osc 1 Fine": -12.0, "Osc 2 Wave": SAW,
            "Osc 2 Level": -10.0, "Osc 2 Fine": 12.0, "Osc 3 Wave": SAW, "Osc 3 Level": -14.0,
            "Osc 3 Coarse": 12.0, "Osc 3 Fine": -5.0, "Osc 4 Wave": SQUARE, "Osc 4 Level": -18.0,
            "Osc 4 Coarse": -12.0, "Filter Type": LOWPASS, "Filter Slope": SLOPE24, "Cutoff": 380.0,
            "Resonance": 0.2, "Vel Filter": 5.0, "Vel Amp": 0.25, "Amp Attack": 250.0, "Amp Decay": 800.0,
            "Amp Sustain": 0.9, "Amp Release": 900.0, "Output": -12.0}},
        "fx": [fx("magda_chorus", Voices=L("2", 1), Rate=0.4, Depth=0.5, Mix=0.45, Width=1.0),
               fx("magda_reverb", Engine=HALL, Mix=0.3, Decay=65.0, **{"Low Cut": 250.0, "High Cut": 9000.0})],
    },
    "pluck": {
        "description": "Short filtered pluck for arps, dotted-eighth echo.",
        "stem": "synths", "volume": 0.225, "role": "arp",
        "instrument": {"device": "magda_polysynth", "params": {
            "Osc 1 Wave": SAW, "Osc 1 Level": -8.0, "Osc 2 Wave": SQUARE, "Osc 2 Level": -12.0,
            "Osc 2 Coarse": 12.0, "Osc 2 Fine": 5.0, **OSC34_OFF, "Filter Type": LOWPASS, "Cutoff": 700.0,
            "Resonance": 0.3, "Filter Env": 3.2, "Filter Attack": 1.0, "Filter Decay": 160.0,
            "Filter Sustain": 0.0, "Filter Release": 120.0, "Amp Attack": 1.0, "Amp Decay": 260.0,
            "Amp Sustain": 0.0, "Amp Release": 150.0, "Vel Amp": 0.6, "Vel Filter": 1.5, "Output": -10.0}},
        "fx": [fx("magda_delay", Sync=SYNC_ON, Division=DOTTED_8TH, Feedback=0.35, Mix=0.22, Cross=0.7),
               fx("magda_reverb", Engine=PLATE, Mix=0.15, Decay=40.0, **{"Low Cut": 300.0})],
    },
    "bell": {
        "description": "Struck bell with a dotted-eighth echo and hall; sparkle arps an octave or two up.",
        "stem": "synths", "volume": 0.25, "role": "bell",
        "instrument": {"device": "magda_bell", "params": {
            "strike_position": 0.4, "strike_tone": 9000.0, "strike_sharpness": 0.5, "decay": 1800.0,
            "Gain": -8.0}},
        "fx": [fx("magda_delay", Sync=SYNC_ON, Division=DOTTED_8TH, Feedback=0.3, Mix=0.2, Cross=0.8),
               fx("magda_reverb", Engine=HALL, Mix=0.35, Decay=60.0, **{"Low Cut": 400.0})],
    },
    "bell_fm": {
        "description": "Glassy two-operator FM bell (ratio 3.5) for fast arps.",
        "stem": "synths", "volume": 0.2, "role": "bell",
        "instrument": {"device": "magda_fm", "params": {
            "Op1 Wave": L("Sine", 0), "Op1 Ratio": 1.0, "Op1 Level": 0.0, "Op2 Ratio": 3.5, "Op2 Level": -60.0,
            "Op3 Level": -60.0, "Op4 Level": -60.0, "M21": 1.2, "Amp Attack": 1.0, "Amp Decay": 450.0,
            "Amp Sustain": 0.0, "Amp Release": 350.0, "Gain": -10.0}},
        "fx": [fx("magda_delay", Sync=SYNC_ON, Division=DOTTED_8TH, Feedback=0.32, Mix=0.2, Cross=0.8),
               fx("magda_reverb", Engine=HALL, Mix=0.3, Decay=55.0, **{"Low Cut": 500.0})],
    },
    # ------------------------------------------------------------------ leads
    "lead_glide": {
        "description": "Bright mono saw/square lead with glide; the main hook voice.",
        "stem": "lead", "volume": 0.3, "role": "lead", "range": [64, 96], "mono": True,
        "instrument": {"device": "magda_polysynth", "params": {
            "Osc 1 Wave": SQUARE, "Osc 1 Level": -8.0, "Osc 1 Fine": -6.0, "Osc 2 Wave": SAW, "Osc 2 Level": -10.0,
            "Osc 2 Coarse": 12.0, "Osc 2 Fine": 7.0, "Osc 3 Wave": SAW, "Osc 3 Level": -14.0, "Osc 3 Fine": 9.0,
            "Osc 4 Level": -60.0, "Osc 4 Enable": L("Off", 0), "Filter Type": LOWPASS, "Cutoff": 5200.0,
            "Resonance": 0.25, "Filter Env": 1.0, "Filter Attack": 2.0, "Filter Decay": 250.0,
            "Filter Sustain": 0.5, "Filter Release": 200.0, "Filter Drive": 0.25, "Amp Attack": 3.0,
            "Amp Decay": 300.0, "Amp Sustain": 0.85, "Amp Release": 140.0, "Voice Mode": LEGATO,
            "Glide": 45.0, "Vel Amp": 0.35, "Vel Filter": 1.0, "Output": -9.0}},
        "fx": [fx("magda_saturator", Mode=L("Soft", 1), Drive=6.0, Mix=0.5),
               fx("magda_delay", Sync=SYNC_ON, Division=DOTTED_8TH, Feedback=0.3, Mix=0.18, Tone=0.2, Cross=0.6),
               fx("magda_reverb", Engine=PLATE, Mix=0.14, Decay=45.0, Predelay=15.0,
                  **{"Low Cut": 300.0, "High Cut": 11000.0})],
    },
    "lead_fm_crushed": {
        "description": "Bitcrushed FM double, usually an octave above the lead.",
        "stem": "lead", "volume": 0.2, "role": "lead",
        "instrument": {"device": "magda_fm", "params": {
            "Op1 Wave": L("Sine", 0), "Op1 Ratio": 1.0, "Op1 Level": 0.0, "M21": 1.6, "M31": 0.4,
            "Op2 Ratio": 2.0, "Op3 Ratio": 3.0, "Op2 Level": -60.0, "Op3 Level": -60.0, "Op4 Level": -60.0,
            "Amp Attack": 2.0, "Amp Decay": 400.0, "Amp Sustain": 0.4, "Amp Release": 250.0, "Gain": -10.0}},
        "fx": [fx("magda_bitcrusher", Bits=7.0, Rate=16000.0, Mix=0.35, Drive=3.0),
               fx("magda_delay", Sync=SYNC_ON, Division=EIGHTH, Feedback=0.35, Mix=0.25, Cross=0.8),
               fx("magda_reverb", Engine=PLATE, Mix=0.2, Decay=50.0, **{"Low Cut": 400.0})],
    },
    "chop_square": {
        "description": "Short, nasal square stab for chopped hook fragments (band-passed, crushed).",
        "stem": "lead", "volume": 0.22, "role": "chop", "range": [70, 100],
        "instrument": {"device": "magda_polysynth", "params": {
            "Osc 1 Wave": SQUARE, "Osc 1 Level": -8.0, "Osc 2 Wave": SAW, "Osc 2 Level": -12.0, "Osc 2 Fine": 8.0,
            **OSC34_OFF, "Filter Type": L("Bandpass", 2), "Cutoff": 1800.0, "Resonance": 0.45,
            "Filter Env": 1.5, "Filter Decay": 120.0, "Filter Sustain": 0.2, "Amp Attack": 1.0,
            "Amp Decay": 160.0, "Amp Sustain": 0.5, "Amp Release": 60.0, "Vel Amp": 0.5, "Output": -6.0}},
        "fx": [fx("magda_eq", **{"Band 4 Enabled": 1.0, "Band 4 Type": L("Bell", 2), "Band 4 Freq": 2600.0,
                                 "Band 4 Gain": 5.0, "Band 4 Q": 1.2}),
               fx("magda_bitcrusher", Bits=8.0, Rate=22000.0, Mix=0.3),
               fx("magda_delay", Sync=SYNC_ON, Division=DOTTED_8TH, Feedback=0.3, Mix=0.25, Cross=0.9),
               fx("magda_reverb", Engine=PLATE, Mix=0.22, Decay=40.0, **{"Low Cut": 400.0})],
    },
    # -------------------------------------------------------------------- fx
    "riser": {
        "description": "Two detuned saws whose filter opens with velocity: write rising chromatic "
                       "steps with rising velocity for a build.",
        "stem": "fx", "volume": 0.25, "role": "riser",
        "instrument": {"device": "magda_polysynth", "params": {
            "Osc 1 Wave": SAW, "Osc 1 Level": -8.0, "Osc 2 Wave": SAW, "Osc 2 Level": -8.0, "Osc 2 Coarse": 12.0,
            "Osc 2 Fine": 15.0, **OSC34_OFF, "Filter Type": LOWPASS, "Cutoff": 400.0, "Resonance": 0.35,
            "Vel Filter": 5.5, "Amp Attack": 5.0, "Amp Sustain": 1.0, "Amp Release": 200.0, "Vel Amp": 0.7,
            "Output": -14.0}},
        "fx": [fx("magda_delay", Sync=SYNC_ON, Division=SIXTEENTH, Feedback=0.3, Mix=0.2, Cross=0.8),
               fx("magda_reverb", Engine=HALL, Mix=0.35, Decay=60.0, **{"Low Cut": 300.0})],
    },
    "noise_swell": {
        "description": "High-passed noise with a long attack: one long note = a white-noise swell.",
        "stem": "fx", "volume": 0.225, "role": "swell",
        "instrument": {"device": "magda_fm", "params": {
            "Op1 Wave": L("Noise", 4), "Op1 Level": 0.0, "M21": 0.0, "Op2 Level": -60.0, "Op3 Level": -60.0,
            "Op4 Level": -60.0, "Amp Attack": 1800.0, "Amp Decay": 2000.0, "Amp Sustain": 1.0,
            "Amp Release": 400.0, "Vel Amount": 1.0, "Gain": -14.0}},
        "fx": [fx("magda_filter", Engine=L("SVF", 0), Mode=L("HP", 2), Cutoff=1500.0, Resonance=0.2),
               fx("magda_reverb", Engine=HALL, Mix=0.4, Decay=60.0, **{"Low Cut": 500.0})],
    },
    "static_crush": {
        "description": "Bitcrushed, band-limited noise bed (TV/radio static). Velocity sets the level.",
        "stem": "fx", "volume": 0.2, "role": "texture",
        "instrument": {"device": "magda_fm", "params": {
            "Op1 Wave": L("Noise", 4), "Op1 Level": 0.0, "M21": 0.0, "Op2 Level": -60.0, "Op3 Level": -60.0,
            "Op4 Level": -60.0, "Amp Attack": 20.0, "Amp Decay": 200.0, "Amp Sustain": 1.0,
            "Amp Release": 80.0, "Vel Amount": 1.0, "Gain": -12.0}},
        "fx": [fx("magda_bitcrusher", Bits=3.0, Rate=9000.0, Drive=4.0, Mix=1.0),
               fx("magda_filter", Engine=L("SVF", 0), Mode=L("BP", 1), Cutoff=2600.0, Resonance=0.15)],
    },
    "beep": {
        "description": "Plain sine beep through a crusher (UI sounds, call-ended tones).",
        "stem": "fx", "volume": 0.2, "role": "texture",
        "instrument": {"device": "magda_fm", "params": {
            "Op1 Wave": L("Sine", 0), "Op1 Level": 0.0, "M21": 0.0, "Op2 Level": -60.0, "Op3 Level": -60.0,
            "Op4 Level": -60.0, "Amp Attack": 2.0, "Amp Decay": 100.0, "Amp Sustain": 1.0,
            "Amp Release": 10.0, "Gain": -12.0}},
        "fx": [fx("magda_bitcrusher", Bits=5.0, Rate=8000.0, Mix=1.0)],
    },
}


def get_preset(name: str) -> dict:
    if name not in PRESETS:
        import difflib
        near = difflib.get_close_matches(name, list(PRESETS), n=3)
        raise KeyError(f"unknown preset {name!r}" + (f"; did you mean {near}?" if near else "")
                       + f" (available: {sorted(PRESETS)})")
    from composerbench.devices import levels
    preset = copy.deepcopy(PRESETS[name])
    preset["level"] = levels.default_level(name, preset.get("role"))
    preset["lufs_at_unity"] = levels.lufs_at_unity(name)
    return preset


def list_presets() -> list[dict]:
    from composerbench.devices import levels
    out = []
    for name, p in PRESETS.items():
        inst = p["instrument"]
        out.append({"name": name, "role": p.get("role"), "stem": p["stem"],
                    "instrument": inst.get("device") or inst.get("plugin"),
                    "fx": [f["device"] for f in p.get("fx", [])],
                    "level": levels.default_level(name, p.get("role")),
                    "description": p["description"]})
    return out
