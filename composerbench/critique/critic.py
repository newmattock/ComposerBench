"""Audio critic (Sprint B2): structured verdicts from section features."""
from __future__ import annotations

# Intent emotion tags -> brightness expectations (centroid Hz bands)
BRIGHTNESS_TARGETS = {
    "serene":   (400, 1800),
    "warm":     (300, 2000),
    "nostalgic": (350, 2200),
    "neutral":  (400, 2500),
    "aggressive": (800, 5000),
    "bright":   (900, 6000),
}

DYNAMICS_TOLERANCE = 1.5     # dB
MASKING_OVERLAP = 0.60       # low-band fraction considered bass-heavy


def _intent_targets(intent_emotion: dict) -> tuple[float, float]:
    overall = ((intent_emotion or {}).get("overall") or ["neutral"])
    lows, highs = [], []
    for tag in overall:
        lo, hi = BRIGHTNESS_TARGETS.get(str(tag).lower(), (400, 2500))
        lows.append(lo)
        highs.append(hi)
    return sum(lows) / len(lows), sum(highs) / len(highs)


def criticize(features: list[dict], sections: list[str],
              intent_emotion: dict | None = None,
              energy_targets: dict[str, float] | None = None) -> list[dict]:
    """Compare per-section features against intent + plan targets.

    Returns findings: {section, dimension, observed, expected, severity,
    hint}. Severity 0..1 (1 = worst).
    """
    findings: list[dict] = []
    lo_t, hi_t = _intent_targets(intent_emotion)

    for sec, f in zip(sections, features):
        # --- brightness ---
        c = f.get("centroid_hz", 0.0)
        if c < lo_t * 0.6:
            findings.append({
                "section": sec, "dimension": "brightness",
                "observed": round(c), "expected": f">= {lo_t:.0f}Hz",
                "severity": min(1.0, (lo_t * 0.6 - c) / max(lo_t, 1)),
                "hint": "section too dark: open a filter or raise velocity",
            })
        elif c > hi_t * 1.8:
            findings.append({
                "section": sec, "dimension": "brightness",
                "observed": round(c), "expected": f"<= {hi_t:.0f}Hz",
                "severity": min(1.0, (c - hi_t * 1.8) / max(hi_t, 1)),
                "hint": "section too bright: close filter, lower wet reverb",
            })

        # --- masking (bass-heavy + dense = mud risk) ---
        if f.get("low_energy", 0) > MASKING_OVERLAP \
                and f.get("transient_density", 0) > 4:
            findings.append({
                "section": sec, "dimension": "masking",
                "observed": round(f["low_energy"], 2),
                "expected": f"< {MASKING_OVERLAP}",
                "severity": (f["low_energy"] - MASKING_OVERLAP)
                            / (1 - MASKING_OVERLAP),
                "hint": "low-end crowding: duck pads/hats or narrow bass",
            })

        # --- dynamics vs plan targets ---
        if energy_targets:
            target = energy_targets.get(sec)
            if target is not None and target > 0:
                expected_db = -20.0 + 12.0 * target      # map E(0..1) -> dBFS
                delta = f.get("rms_dbfs", -90) - expected_db
                if abs(delta) > DYNAMICS_TOLERANCE + 6:
                    findings.append({
                        "section": sec, "dimension": "level",
                        "observed": round(f["rms_dbfs"], 1),
                        "expected": f"~{expected_db:.1f}dBFS",
                        "severity": min(1.0, abs(delta) / 24),
                        "hint": ("section louder than planned"
                                 if delta > 0 else
                                 "section quieter than planned"),
                    })

    # --- arc monotonicity across consecutive rising targets ---
    if energy_targets:
        order = [s for s in sections if s in energy_targets]
        for prev, nxt in zip(order, order[1:]):
            pe = energy_targets.get(prev, 0)
            ne = energy_targets.get(nxt, 0)
            fp = next((f for f, s in zip(features, sections) if s == prev), None)
            fn = next((f for f, s in zip(features, sections) if s == nxt), None)
            if fp and fn and ne > pe + 0.05 and fn["rms_dbfs"] <= fp["rms_dbfs"] + DYNAMICS_TOLERANCE:
                findings.append({
                    "section": nxt, "dimension": "arc",
                    "observed": round(fn["rms_dbfs"], 1),
                    "expected": f"> {fp['rms_dbfs']:.1f}dBFS (+{DYNAMICS_TOLERANCE})",
                    "severity": 0.7,
                    "hint": f"'{nxt}' fails to rise above '{prev}' — "
                            f"add notes/velocity or lift the lead register",
                })
    return findings
