"""Sample-library registry + canonical drum vocabulary (Sprints 3-4).

The agent speaks musical names ("kick", "snare", "closed_hat"); this module
maps them to each library's actual MIDI notes. Libraries are declared in
~/.musicbench/libraries/*.yaml and resolved by id.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Canonical vocabulary -> General MIDI percussion notes. AVL Drumkits (and
# most acoustic kits) follow GM on these, so the defaults are right without
# per-library overrides; manifests may still remap any entry.
DRUM_VOCAB: dict[str, int] = {
    "kick": 36,
    "snare": 38,
    "closed_hat": 42,
    "open_hat": 46,
    "ride": 51,
    "crash": 49,
    "tom_high": 50,
    "tom_mid": 47,
    "tom_low": 45,
}

GLOBAL_LIBRARIES_DIR = Path.home() / ".musicbench" / "libraries"
GLOBAL_DEVICES_DIR = Path.home() / ".musicbench" / "devices"


@dataclass
class Library:
    id: str
    engine: str                      # e.g. "shortcircuit_xt"
    path: str                        # absolute path to patch file (.sfz ...)
    format: str = "sfz"
    family: str = ""
    subfamily: str = ""
    style: list[str] = field(default_factory=list)
    license: dict = field(default_factory=dict)
    velocity_layers: int = 1
    mapping: dict[str, int] = field(default_factory=dict)  # vocab overrides

    def note_for(self, instrument: str) -> int:
        if instrument in self.mapping:
            return int(self.mapping[instrument])
        if instrument in DRUM_VOCAB:
            return DRUM_VOCAB[instrument]
        raise KeyError(
            f"unknown drum instrument {instrument!r}; "
            f"vocabulary: {sorted(DRUM_VOCAB)}"
        )

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "engine": self.engine,
            "format": self.format,
            "path": self.path,
        }
        for k in ("family", "subfamily"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        if self.style:
            d["style"] = self.style
        if self.license:
            d["license"] = self.license
        d["velocity_layers"] = self.velocity_layers
        if self.mapping:
            d["mapping"] = self.mapping
        return d


def _library_from_doc(doc: dict) -> Library:
    return Library(
        id=doc["id"],
        engine=doc.get("engine", "shortcircuit_xt"),
        path=doc["path"],
        format=doc.get("format", "sfz"),
        family=doc.get("family", ""),
        subfamily=doc.get("subfamily", ""),
        style=list(doc.get("style", [])),
        license=dict(doc.get("license", {})),
        velocity_layers=int(doc.get("velocity_layers", 1)),
        mapping={k: int(v) for k, v in doc.get("mapping", {}).items()},
    )


def libraries_dir() -> Path:
    return GLOBAL_LIBRARIES_DIR


def load_libraries() -> dict[str, Library]:
    """Load every registered library manifest (JSON or YAML)."""
    out: dict[str, Library] = {}
    if not GLOBAL_LIBRARIES_DIR.exists():
        return out
    try:
        import yaml  # type: ignore
        have_yaml = True
    except ImportError:
        have_yaml = False
    for f in sorted(GLOBAL_LIBRARIES_DIR.glob("*")):
        if f.suffix not in (".yaml", ".yml", ".json"):
            continue
        try:
            if f.suffix == ".json" or not have_yaml:
                doc = json.loads(f.read_text())
            else:
                import yaml  # type: ignore
                doc = yaml.safe_load(f.read_text())
            lib = _library_from_doc(doc)
            out[lib.id] = lib
        except Exception as e:  # noqa: BLE001 - one bad manifest must not kill listing
            print(f"warning: skipping bad library manifest {f}: {e}")
    return out


def get_library(library_id: str) -> Library:
    libs = load_libraries()
    if library_id in libs:
        lib = libs[library_id]
        p = Path(lib.path).expanduser()
        if not p.exists():
            raise FileNotFoundError(
                f"library {library_id!r} sample file missing: {p}"
            )
        lib.path = str(p)
        return lib
    raise KeyError(
        f"unknown library {library_id!r}; registered: {sorted(libs)}"
    )


def write_default_manifests() -> list[Path]:
    """Register the installed AVL kits if their SFZ files exist."""
    GLOBAL_LIBRARIES_DIR.mkdir(parents=True, exist_ok=True)
    home = Path.home()
    candidates = [
        {
            "id": "drums.avl.black_pearl",
            "engine": "shortcircuit_xt",
            "format": "sfz",
            "path": str(home / "Samples/AVL/BLACK_PEARL_2023_repack/Black_Pearl_2023_repack.sfz"),
            "family": "drums",
            "subfamily": "acoustic",
            "style": ["rock", "general"],
            "license": {"type": "CC-BY-SA", "source": "bandshed.net (AVL Drumkits)"},
            "velocity_layers": 5,
        },
        {
            "id": "drums.avl.red_zeppelin",
            "engine": "shortcircuit_xt",
            "format": "sfz",
            "path": str(home / "Samples/AVL/RED_ZEPPELIN_2023_repack/Red_Zeppelin_2023_repack.sfz"),
            "family": "drums",
            "subfamily": "acoustic",
            "style": ["rock"],
            "license": {"type": "CC-BY-SA", "source": "bandshed.net (AVL Drumkits)"},
            "velocity_layers": 5,
        },
    ]
    written = []
    for doc in candidates:
        if not Path(doc["path"]).exists():
            continue
        out = GLOBAL_LIBRARIES_DIR / f"{doc['id']}.json"
        out.write_text(json.dumps(doc, indent=2))
        written.append(out)
    return written
