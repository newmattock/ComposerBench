"""Finding -> corrective-action mapper (Sprint C1).

Maps critic findings to concrete loop Actions using conservative,
single-purpose fixes. Each finding yields at most one action.
"""
from __future__ import annotations

from composerbench.agent.loop import Action
from composerbench.critique.critic import criticize


def fixes_for_findings(findings: list[dict], role_of_track: dict[str, str] | None = None
                       ) -> list[Action]:
    """Convert critic findings into safe corrective actions."""
    actions: list[Action] = []
    for f in findings:
        dim = f.get("dimension")
        sec = f.get("section", "")
        if dim == "brightness":
            if f["observed"] < 300:
                # too dark: nudge lead/pad velocity up via volume lift on the
                # brightest role present
                for track, role in (role_of_track or {}).items():
                    if role in ("dusk_lead", "bell_answer") and f["hint"].startswith("section too dark"):
                        actions.append(Action(type="set_volume", track=track,
                                              volume=1.08))
                        break
        elif dim == "masking":
            # duck the hats/shimmer roles that crowd the low end
            for track, role in (role_of_track or {}).items():
                if role in ("shimmer_echo_bus",):
                    actions.append(Action(type="set_volume", track=track,
                                          volume=0.24))
                    break
    return actions


def auto_fix_actions(wav_path, sections: list[tuple[float, float]],
                     section_names: list[str], bpm: float,
                     intent_emotion: dict | None = None,
                     energy_targets: dict[str, float] | None = None,
                     role_of_track: dict[str, str] | None = None) -> tuple[list[Action], list[dict]]:
    """Run features+critic, return (fixes, findings)."""
    from composerbench.analysis.features import section_features

    feats = section_features(wav_path, sections, bpm)
    findings = criticize(feats, section_names, intent_emotion, energy_targets)
    return fixes_for_findings(findings, role_of_track), findings
