"""Heuristic section-level energy estimate (spec §31, §51).

HEURISTIC, not measurement: never present these numbers as objective truth.
Inputs are symbolic; audio loudness can be folded in later (spec §30).
"""

from __future__ import annotations

from composerbench.analysis.symbolic import SymbolicStats

# Tunable weights — documented so agents can reason about the model itself.
WEIGHTS = {
    "density": 0.35,      # notes per bar (saturating at 32/bar)
    "velocity": 0.25,     # mean velocity / 127
    "polyphony": 0.15,    # saturating at 8 simultaneous voices
    "register": 0.10,     # median pitch between A0(21)..C8(108)
    "activity": 0.15,     # 1 - silence_ratio
}

DENSITY_SATURATION = 32.0
POLYPHONY_SATURATION = 8.0
PITCH_LOW, PITCH_HIGH = 21.0, 108.0


def estimate_energy(stats: SymbolicStats) -> float:
    """Map symbolic stats onto a 0..1 energy score."""
    if stats.note_count == 0 or stats.span_beats <= 0:
        return 0.0
    density_n = min(stats.density_per_bar / DENSITY_SATURATION, 1.0)
    velocity_n = (stats.velocity_mean or 0.0) / 127.0
    poly_n = min(stats.max_polyphony / POLYPHONY_SATURATION, 1.0)
    reg_n = ((stats.median_pitch or PITCH_LOW) - PITCH_LOW) / (PITCH_HIGH - PITCH_LOW)
    activity_n = 1.0 - stats.silence_ratio
    score = (
        WEIGHTS["density"] * density_n
        + WEIGHTS["velocity"] * velocity_n
        + WEIGHTS["polyphony"] * poly_n
        + WEIGHTS["register"] * reg_n
        + WEIGHTS["activity"] * activity_n
    )
    return max(0.0, min(1.0, score))
