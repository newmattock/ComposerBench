"""Instrument capability knowledge: verified-audible allowlist + manifests.

Two sources of truth, consulted in order:

1. A doctor manifest (<project>/.musicbench/instrument_manifest.json) written
   by `composerbench doctor`, which renders one probe note through each candidate
   instrument against a specific MAGDA binary and records what actually
   produced sound.
2. The built-in KNOWN_AUDIBLE_INSTRUMENTS allowlist, verified live against
   magda-core @ ebd93db0. Over the CLI, only these internal instrument ids are
   audible; waveform-flavored names like square/pulse/saw/noise are *accepted*
   by the CLI but render digital silence (-120 dBFS).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Verified live (probe renders produced audible WAVs): each entry below is a
# real instrument that responds to MIDI notes over the CLI. Waveform-flavored
# names (square, pulse, saw, noise, triangle, sine, sid, chip) are proven
# SILENT and deliberately excluded. Compiled *effects* (magda_reverb,
# magda_delay, ...) exist in the plugin catalog but are NOT instruments -
# add-internal-instrument with them yields no MIDI-driven sound.
KNOWN_AUDIBLE_INSTRUMENTS = (
    "4osc",                                        # FourOsc substrate synth
    "magda_polysynth", "magda_fm",                 # synths
    "magda_marimba", "magda_bell",                 # melodic percussion
    "magda_kick", "magda_snare", "magda_clap",
    "magda_hat", "magda_tom", "magda_djembe",      # drum kit
)

# Escape hatch for probing experimental binaries: set to any non-empty value.
ALLOW_ANY_ENV = "COMPOSERBENCH_ALLOW_ANY_INSTRUMENT"


def manifest_path_for(project_dir: Path | str) -> Path:
    return Path(project_dir) / ".musicbench" / "instrument_manifest.json"


GLOBAL_MANIFEST = Path.home() / ".musicbench" / "instrument_manifest.json"


def allow_any_instrument() -> bool:
    """Escape hatch: COMPOSERBENCH_ALLOW_ANY_INSTRUMENT=1 skips the guard."""
    return bool(os.environ.get(ALLOW_ANY_ENV))


def load_manifest(project_dir: Path | str) -> dict | None:
    path = manifest_path_for(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def audible_instruments_for(project_dir: Path | str) -> tuple[frozenset[str], str]:
    """(set of known-audible instrument ids, source description).

    Consults, in order: the project-local doctor manifest, the global doctor
    manifest (~/.musicbench/instrument_manifest.json), then the built-in
    verified allowlist.
    """
    for source_label, manifest in (
        ("project manifest", load_manifest(project_dir)),
        ("global manifest", _load_global_manifest()),
    ):
        if manifest:
            audible = {
                str(entry["id"]).strip().lower()
                for entry in manifest.get("instruments", [])
                if entry.get("audible")
            }
            if audible:
                label = source_label
                if "binary" in manifest:
                    label += f" ({manifest['binary']})"
                return frozenset(audible), label
    return frozenset(KNOWN_AUDIBLE_INSTRUMENTS), "built-in allowlist"


def _load_global_manifest() -> dict | None:
    if not GLOBAL_MANIFEST.exists():
        return None
    try:
        return json.loads(GLOBAL_MANIFEST.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_manifest(project_dir: Path | str, data: dict) -> Path:
    path = manifest_path_for(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    return path


