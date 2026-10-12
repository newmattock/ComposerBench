"""Offline knowledge of MAGDA's devices: names, parameters, ranges and labels.

The packaged ``catalog.json`` comes from a live engine probe plus magda-core's
sources (see ``build_catalog.py``). ``composerbench devices refresh`` re-probes a
running engine and writes ``~/.musicbench/devices.json``, which then takes
precedence. Nothing here needs a running engine: agents use it to look up
parameter names before building, and the song builder uses it to preflight a
spec so typos fail in milliseconds instead of halfway through a build.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PACKAGED_CATALOG = Path(__file__).with_name("catalog.json")
USER_CATALOG = Path.home() / ".musicbench" / "devices.json"


def load_catalog(user: bool = True) -> dict:
    """The user catalog when present (and ``user``), else the packaged one."""
    return _load(str(USER_CATALOG) if user and USER_CATALOG.exists() else str(PACKAGED_CATALOG))


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def canonical_name(name: str, catalog: dict | None = None) -> str:
    cat = catalog or load_catalog()
    if name in cat["devices"]:
        return name
    if name in cat.get("aliases", {}):
        return cat["aliases"][name]
    low = name.strip().lower()
    for dev in cat["devices"]:
        if dev.lower() == low:
            return dev
    for alias, dev in cat.get("aliases", {}).items():
        if alias.lower() == low:
            return dev
    return name


def device(name: str, catalog: dict | None = None) -> dict | None:
    cat = catalog or load_catalog()
    entry = cat["devices"].get(canonical_name(name, cat))
    return None if entry is None else {"name": canonical_name(name, cat), **entry}


def list_devices(kind: str | None = None, catalog: dict | None = None) -> list[dict]:
    cat = catalog or load_catalog()
    return [{"name": n, "kind": d["kind"], "format": d.get("format", "internal"),
             "verified": d.get("verified"), "params": len(d.get("params", [])),
             "summary": d.get("summary", "")}
            for n, d in cat["devices"].items() if kind in (None, d["kind"])]


def find_param(entry: dict, name: str) -> dict | None:
    params = entry.get("params", [])
    for p in params:
        if p["name"] == name:
            return p
    low = name.strip().lower()
    for p in params:
        if p["name"].lower() == low:
            return p
    return None


def label_index(param: dict, label: str) -> int | None:
    """Index of a label: exact, then ignoring case/spaces, then unique prefix."""
    choices = param.get("choices") or []
    if label in choices:
        return choices.index(label)
    norm = [c.replace(" ", "").lower() for c in choices]
    key = label.replace(" ", "").lower()
    if key in norm:
        return norm.index(key)
    starts = [i for i, c in enumerate(norm) if c.startswith(key)]
    return starts[0] if len(starts) == 1 else None


@dataclass
class ParamCheck:
    device: str
    param: str
    ok: bool
    value: object = None
    resolved: float | None = None
    message: str = ""
    severity: str = "ok"             # ok | info | warning | error

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "")}


def check_value(device_name: str, param_name: str, value, catalog: dict | None = None) -> ParamCheck:
    """Validate one parameter value against the catalog.

    Numbers must fall inside [min, max] (out-of-range values are reported as
    warnings because the builder clamps them). Strings must be one of the
    parameter's labels. ``{"label": ..., "value": ...}`` passes when either part
    is valid. Devices or parameters the catalog does not know are warnings, not
    errors: the live engine is the final authority.
    """
    entry = device(device_name, catalog)
    if entry is None:
        return ParamCheck(device_name, param_name, True, value, None,
                          "device not in catalog; cannot preflight", "warning")
    p = find_param(entry, param_name)
    if p is None:
        if entry.get("format", "internal") != "internal" or not entry.get("params"):
            return ParamCheck(device_name, param_name, True, value, None,
                              "parameter not catalogued for this device", "info")
        import difflib
        names = [q["name"] for q in entry["params"]]
        near = difflib.get_close_matches(param_name, names, n=4, cutoff=0.5)
        return ParamCheck(device_name, param_name, False, value, None,
                          f"unknown parameter; did you mean {near}?" if near else "unknown parameter",
                          "error")
    if isinstance(value, dict):
        label, number = value.get("label"), value.get("value")
        checks = [check_value(device_name, p["name"], v, catalog) for v in (label, number) if v is not None]
        good = [c for c in checks if c.ok and c.severity != "warning"]
        if good:
            return ParamCheck(device_name, p["name"], True, value, good[0].resolved)
        return checks[0] if checks else ParamCheck(device_name, p["name"], False, value, None,
                                                   "needs 'label' or 'value'", "error")
    if isinstance(value, str):
        idx = label_index(p, value)
        if idx is None:
            if not p.get("choices"):
                return ParamCheck(device_name, p["name"], False, value, None,
                                  "parameter has no labels; give a number "
                                  f"in [{p.get('min')}, {p.get('max')}]", "error")
            return ParamCheck(device_name, p["name"], False, value, None,
                              f"unknown label; choose from {p['choices']}", "error")
        return ParamCheck(device_name, p["name"], True, value, float(idx))
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ParamCheck(device_name, p["name"], False, value, None,
                          "value must be a number or a label", "error")
    lo, hi = p.get("min"), p.get("max")
    if lo is not None and hi is not None and not (lo - 1e-9 <= v <= hi + 1e-9):
        return ParamCheck(device_name, p["name"], True, value, min(hi, max(lo, v)),
                          f"out of range [{lo}, {hi}]; will be clamped", "warning")
    return ParamCheck(device_name, p["name"], True, value, v)


@dataclass
class DeviceDoc:
    """Compact, agent-readable description of one device."""
    name: str
    entry: dict
    lines: list[str] = field(default_factory=list)

    def render(self) -> str:
        e = self.entry
        out = [f"{self.name} ({e['kind']}, {e.get('format', 'internal')}, verified: {e.get('verified')})"]
        if e.get("summary"):
            out.append(e["summary"])
        if e.get("notes"):
            out.append("Note: " + e["notes"])
        for p in e.get("params", []):
            rng = "" if p.get("min") is None else f" [{p['min']:g}..{p['max']:g}]"
            unit = f" {p['unit']}" if p.get("unit") else ""
            lab = f" {{{' | '.join(p['choices'])}}}" if p.get("choices") else ""
            dflt = "" if p.get("default") is None else f" default {p['default']:g}"
            note = f"  ({p['note']})" if p.get("note") else ""
            out.append(f"  - {p['name']}{rng}{unit}{lab}{dflt}{note}")
        return "\n".join(out)


def describe(name: str, catalog: dict | None = None) -> str:
    entry = device(name, catalog)
    if entry is None:
        return f"{name}: not in the device catalog"
    return DeviceDoc(entry["name"], entry).render()
