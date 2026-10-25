"""Parameter meaning: labels, display text and value resolution.

Agents should be able to say ``Engine = "Hall"`` or ``Division = "1/8."``
instead of guessing that the 8th option is a dotted eighth, and they should
see what a number *means* ("LP 12 dB", "5.8 kHz") before and after they set it.

Parameter metadata dictionaries (as returned by adapter ``get_parameters``)
may carry any of:

    name, value, min, max, unit, default,
    choices       list of labels for discrete parameters (index order)
    choiceValues  native value at which each label sits (optional; engines
                  that sample a hosted plugin's display text report these)
    steps         number of discrete states (0 or missing = continuous)
    text          the plugin's display text for the current value
    scale         "linear" | "log" | "discrete" | "boolean" | ...
    owner         "host" for the engine's wrapper parameters (Tracktion puts
                  a Dry Level / Wet Level pair in front of every hosted
                  plugin's own parameters); missing = the plugin's own

Everything here is pure and adapter-independent so it can be unit tested.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from composerbench.errors import ComposerBenchError, EntityNotFound


class UnknownParameterLabel(ComposerBenchError, ValueError):
    code = "UNKNOWN_PARAMETER_LABEL"


class ParameterValueInvalid(ComposerBenchError, ValueError):
    code = "PARAMETER_VALUE_INVALID"


class ParameterNotFound(EntityNotFound, LookupError):
    """Unknown or ambiguous parameter reference (lists close matches)."""


def _norm(label: str) -> str:
    return " ".join(str(label).strip().lower().split())


def choices_of(meta: dict[str, Any]) -> list[str]:
    choices = meta.get("choices") or []
    return [str(c) for c in choices]


def choice_values_of(meta: dict[str, Any]) -> list[float] | None:
    """Explicit native value per choice, when the engine reported them."""
    values = meta.get("choiceValues") or meta.get("choice_values")
    if values and len(values) == len(choices_of(meta)):
        return [float(v) for v in values]
    return None


def is_host_param(meta: dict[str, Any]) -> bool:
    """True for engine wrapper parameters (not the hosted plugin's own)."""
    return meta.get("owner") == "host" or bool(meta.get("wrapperRole"))


HOST_NOTE = "engine wrapper gain on the whole plugin output, not the plugin's own mix control"


def annotate_host(meta: dict[str, Any]) -> dict[str, Any]:
    """Give a wrapper parameter its own address ("host:Wet Level") and a note."""
    if is_host_param(meta):
        meta["owner"] = "host"
        meta["stableId"] = f"host:{meta.get('name', '')}"
        meta.setdefault("note", HOST_NOTE)
    return meta


def mark_host_wrapper(params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag Tracktion's Dry/Wet wrapper pair at the head of a hosted plugin's
    parameter list, so "Wet Level" means the plugin's own control and the
    wrapper stays reachable as "host:Wet Level"."""
    for p in params[:2]:
        if p.get("name") in ("Dry Level", "Wet Level") and "owner" not in p:
            p["owner"] = "host"
            annotate_host(p)
    return params


def _bounds(meta: dict[str, Any]) -> tuple[float, float]:
    if "min" in meta or "max" in meta:
        return float(meta.get("min", 0.0)), float(meta.get("max", 1.0))
    rng = meta.get("range")
    if rng:
        return float(rng[0]), float(rng[1])
    return 0.0, 1.0


def index_to_value(meta: dict[str, Any], index: int) -> float:
    """Native value for the ``index``-th choice.

    MAGDA's internal discrete parameters store the index itself
    (range ``[0, n-1]``); hosted plugins store a normalised 0..1 value where
    choice ``i`` of ``n`` sits at ``i / (n - 1)``.
    """
    choices = choices_of(meta)
    n = len(choices) or int(meta.get("steps") or 0)
    if n < 1 or not 0 <= index < n:
        raise ParameterValueInvalid(parameter=meta.get("name"), index=index, states=n)
    explicit = choice_values_of(meta)
    if explicit is not None:
        return explicit[index]
    lo, hi = _bounds(meta)
    if n == 1:
        return lo
    if math.isclose(hi - lo, n - 1, abs_tol=1e-6):
        return lo + index
    return lo + (hi - lo) * index / (n - 1)


def value_to_index(meta: dict[str, Any], value: float) -> int | None:
    choices = choices_of(meta)
    n = len(choices) or int(meta.get("steps") or 0)
    if n < 1:
        return None
    explicit = choice_values_of(meta)
    if explicit is not None:
        return min(range(n), key=lambda i: abs(explicit[i] - float(value)))
    lo, hi = _bounds(meta)
    if n == 1 or hi == lo:
        return 0
    if math.isclose(hi - lo, n - 1, abs_tol=1e-6):
        pos = float(value) - lo
    else:
        pos = (float(value) - lo) / (hi - lo) * (n - 1)
    return int(min(n - 1, max(0, round(pos))))


def label_for(meta: dict[str, Any], value: float | None = None) -> str | None:
    """Human meaning of ``value`` (default: current value)."""
    if value is None:
        if meta.get("text"):
            return str(meta["text"])
        value = meta.get("value")
    if value is None:
        return None
    choices = choices_of(meta)
    if choices:
        idx = value_to_index(meta, float(value))
        return choices[idx] if idx is not None else None
    return None


def resolve_value(meta: dict[str, Any], requested: Any) -> float:
    """Turn a number, numeric string or choice label into the native value.

    Raises UnknownParameterLabel (listing the valid labels) or
    ParameterValueInvalid (with the legal range) instead of letting a bad
    value reach the engine.
    """
    name = meta.get("name")
    choices = choices_of(meta)
    if isinstance(requested, bool):
        requested = float(requested)
    if isinstance(requested, str):
        text = requested.strip()
        try:
            requested = float(text)
        except ValueError:
            if not choices:
                raise UnknownParameterLabel(
                    parameter=name, requested=text, choices=[],
                    hint="this parameter is continuous; pass a number within its range",
                    range=list(_bounds(meta)), unit=meta.get("unit", "")) from None
            want = _norm(text)
            exact = [i for i, c in enumerate(choices) if _norm(c) == want]
            if not exact:
                exact = [i for i, c in enumerate(choices) if _norm(c).replace(" ", "") == want.replace(" ", "")]
            if not exact:
                exact = [i for i, c in enumerate(choices) if _norm(c).startswith(want)]
            if len(exact) != 1:
                raise UnknownParameterLabel(parameter=name, requested=text, choices=choices,
                                            ambiguous=len(exact) > 1) from None
            return index_to_value(meta, exact[0])
    try:
        value = float(requested)
    except (TypeError, ValueError):
        raise ParameterValueInvalid(parameter=name, received=requested) from None
    if not math.isfinite(value):
        raise ParameterValueInvalid(parameter=name, received=requested, reason="not finite")
    lo, hi = _bounds(meta)
    if not lo - 1e-9 <= value <= hi + 1e-9:
        raise ParameterValueInvalid(parameter=name, received=value, allowed=[lo, hi],
                                    unit=meta.get("unit", ""), choices=choices or None)
    return min(hi, max(lo, value))


def describe(meta: dict[str, Any]) -> dict[str, Any]:
    """Compact, agent-facing summary of one parameter."""
    lo, hi = _bounds(meta)
    out: dict[str, Any] = {"name": meta.get("name"), "value": meta.get("value"),
                           "min": lo, "max": hi}
    for key in ("unit", "default", "steps", "scale", "stableId", "owner", "note"):
        if meta.get(key) not in (None, "", []):
            out[key] = meta[key]
    choices = choices_of(meta)
    if choices:
        out["choices"] = choices
    meaning = label_for(meta)
    if meaning is not None:
        out["text"] = meaning
    return out


def find(params: dict[Any, dict[str, Any]] | Iterable[dict[str, Any]], ref: Any) -> tuple[Any, dict[str, Any]]:
    """Look a parameter up by index, exact name, stable id or unique case-insensitive name."""
    items = list(params.items()) if isinstance(params, dict) else list(enumerate(params))
    ref_s = str(ref)
    hits = [(k, p) for k, p in items if str(k) == ref_s or p.get("name") == ref_s or p.get("stableId") == ref_s]
    if not hits:
        hits = [(k, p) for k, p in items if _norm(p.get("name", "")) == _norm(ref_s)]
    if len(hits) > 1:
        # "Wet Level" means the plugin's own control; the engine's wrapper
        # pair stays addressable by stable id ("host:Wet Level") or index.
        own = [(k, p) for k, p in hits if not is_host_param(p)]
        if len(own) == 1:
            hits = own
    if len(hits) != 1:
        close = [p.get("stableId") if is_host_param(p) else p.get("name")
                 for _, p in items if _norm(ref_s) in _norm(p.get("name", ""))][:12]
        raise ParameterNotFound("parameter", ref_s, close)
    return hits[0]


def match(params: dict[Any, dict[str, Any]] | Iterable[dict[str, Any]], names: Iterable[str],
          owner: str | None = None) -> tuple[Any, dict[str, Any]] | None:
    """Fuzzy lookup for authored recipes: candidate ``names`` are tried in
    order, exact (case-insensitive) matches before substring matches, and the
    plugin's own parameters before the engine's wrapper parameters.

    ``owner="host"`` restricts the search to wrapper parameters and
    ``owner="plugin"`` to the plugin's own. Returns None when nothing fits.
    """
    items = list(params.items()) if isinstance(params, dict) else list(enumerate(params))
    wanted = [_norm(n) for n in names if str(n).strip()]
    if owner == "host":
        pools = [[(k, p) for k, p in items if is_host_param(p)]]
    elif owner == "plugin":
        pools = [[(k, p) for k, p in items if not is_host_param(p)]]
    else:
        pools = [[(k, p) for k, p in items if not is_host_param(p)],
                 [(k, p) for k, p in items if is_host_param(p)]]
    for pool in pools:
        for w in wanted:
            for k, p in pool:
                if _norm(p.get("name", "")) == w:
                    return k, p
        for w in wanted:
            for k, p in pool:
                if w in _norm(p.get("name", "")):
                    return k, p
    return None
