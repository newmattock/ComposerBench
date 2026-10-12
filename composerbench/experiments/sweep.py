"""Experiment sweep harness (Sprint E1): render a param axis, score, learn.

For a role+parameter axis: render N variants through the serve engine,
extract a brightness response (high-band energy share; centroid fallback),
and persist learned overrides to ~/.musicbench/semantics/learned/.
"""
from __future__ import annotations

import json
from pathlib import Path

from composerbench.adapter.base import RenderRequest
from composerbench.semantics.registry import RoleRecipe, SemanticRegistry
from composerbench.analysis.features import section_features


def _clone_with(recipe: RoleRecipe, names: list[str], value: float) -> RoleRecipe:
    data = recipe.model_dump()
    for p in data["params"]:
        if set(p["names"]) & set(names):
            p["value"] = value
    return RoleRecipe(**data)


async def sweep(daw, track_name: str, recipe: RoleRecipe,
                registry: SemanticRegistry,
                param_names: list[str], values: list[float],
                section_bounds: list[tuple[float, float]], bpm: float,
                out_dir: Path) -> list[dict]:
    """Sweep one parameter across values; returns [{value, brightness, ...}]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    clip = await daw.create_clip(track_name, "sweep", 0.0,
                                 max(b for _, b in section_bounds))
    from composerbench.model import Note
    # Two-octave run so harmonics extend into the high band — single-register
    # probes barely move any brightness metric under a low-pass.
    probe_notes = [Note(start=float(i) * 0.5, duration=0.4,
                        pitch=48 + (i % 24), velocity=110)
                   for i in range(int(max(b for _, b in section_bounds)) * 2)]
    await daw.add_notes(track_name, clip.id, probe_notes)

    plugin_id = registry.resolve_engine(recipe.engine)
    dev = await daw.add_plugin(track_name, plugin_id, as_instrument=True)
    try:
        # Reproduce the rest of the role first: a swept value only means
        # something in the recipe's context (e.g. a cutoff needs the filter
        # switched on -- Surge's init patch leaves it Off).
        context = context_specs(recipe, param_names)
        if context:
            shaped = await daw.shape_params(track_name, dev, context)
            failed = [r for r in shaped if not r.get("param") or r.get("error")]
            if failed:
                raise ValueError(f"could not reproduce the role context: {failed}")
        for v in values:
            # Surge has per-scene filters (A/B); shape every cutoff-named
            # parameter together so the audible path is definitely affected.
            matched = await daw.shape_all_params(dev, param_names, v)
            if not matched:
                continue
            out = out_dir / f"sweep_{v:.2f}.wav"
            await daw.render(RenderRequest(output_path=out))
            feats = section_features(out, section_bounds, bpm)
            mid = feats[len(feats) // 2] if feats else {}
            rows.append({"value": v, "matched_param": matched,
                         "centroid_hz": round(mid.get("centroid_hz", 0), 1),
                         "high_energy": round(mid.get("high_energy", 0), 4),
                         "rms_dbfs": round(mid.get("rms_dbfs", -120), 1)})
            print(f"  cutoff={v}: high={rows[-1]['high_energy']} "
                  f"centroid={rows[-1]['centroid_hz']}", flush=True)
    finally:
        await daw.remove_plugin(dev)
        cleaner = getattr(daw, "delete_clip", None)
        if cleaner is not None:
            try:
                await cleaner(clip.id)
            except Exception:
                pass  # cosmetic for the sweep
    return rows


def context_specs(recipe: RoleRecipe, param_names: list[str]) -> list:
    """The recipe's other parameters: what must hold while one is swept."""
    return [p for p in recipe.params if not p.overlaps(param_names)]


def learn_brightness_mapping(rows: list[dict]) -> dict | None:
    """Pick the value whose brightness lands closest to the middle of the
    observed range. Metric: high-band share (>2 kHz), centroid fallback.
    Returns None when the parameter had no audible effect."""
    if len(rows) < 3:
        return None
    key = "high_energy" if all("high_energy" in r for r in rows) \
        else "centroid_hz"
    vals = [r[key] for r in rows]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-6:
        return None   # no audible effect on this probe
    target = (lo + hi) / 2
    best = min(rows, key=lambda r: abs(r[key] - target))
    return {"value": best["value"], "metric": key,
            "picked": best[key], "range": [lo, hi],
            "response": [{"value": r["value"], key: r[key]} for r in rows]}


def save_learned(role_slug: str, param_names: list[str], pick: dict,
                 learned_dir: Path, recipe: RoleRecipe | None = None) -> Path:
    """Persist a learned value together with the context it was learned in
    (the recipe's other parameters), so SemanticRegistry.merge_learned can
    refuse it once the recipe changes."""
    learned_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "role": role_slug,
        "params": [{"names": list(param_names)
                    + [p.title() for p in param_names],
                    "value": pick["value"]}],
        "evidence": {"metric": pick.get("metric"),
                     "brightness_range": pick.get("range"),
                     "picked": pick.get("picked")},
    }
    if recipe is not None:
        doc["context"] = [p.model_dump(include={"names", "label", "value"}, exclude_none=True)
                          for p in context_specs(recipe, param_names)]
    out = learned_dir / f"{role_slug}.json"
    out.write_text(json.dumps(doc, indent=1))
    return out
