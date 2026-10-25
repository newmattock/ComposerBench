"""Finding the MAGDA engine binary (``magda_cli``).

One place for every caller (CLI, MCP server, song builder, tests, examples):
an explicit path, then ``$COMPOSERBENCH_CLI``, then the usual build folders
of a ``~/magda-core`` checkout on macOS and Linux (CLion, plain CMake and the
``build-linux`` folder used by docs/BUILDING_MAGDA.md).
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "COMPOSERBENCH_CLI"
MAGDA_ROOT_ENV = "COMPOSERBENCH_MAGDA_ROOT"

_BUILD_DIRS = ("cmake-build-debug", "cmake-build-release", "cmake-build-relwithdebinfo",
               "build", "build-linux", "build-release")
_CONFIGS = ("Release", "Debug", "RelWithDebInfo")


def magda_root() -> Path:
    """The magda-core checkout: ``$COMPOSERBENCH_MAGDA_ROOT`` or ``~/magda-core``."""
    return Path(os.environ.get(MAGDA_ROOT_ENV) or "~/magda-core").expanduser()


def candidates() -> list[Path]:
    """Every build location searched, most specific first."""
    root = magda_root()
    out = []
    for build in _BUILD_DIRS:
        for config in _CONFIGS:
            out.append(root / build / "magda" / "daw" / "magda_cli_artefacts" / config / "magda_cli")
    return out


# kept for callers that imported the old tuple from composerbench.mcp.server
CLI_CANDIDATES = tuple(str(p) for p in candidates())


def _usable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def discover_cli(explicit: str | os.PathLike | None = None) -> Path | None:
    """The magda_cli binary, or None when no engine is installed.

    An explicit path is trusted as given (if it exists). Otherwise
    ``$COMPOSERBENCH_CLI``, then the newest binary among the known build dirs,
    so a fresh Release build wins over a stale Debug one.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path
    env = os.environ.get(ENV_VAR, "")
    if env and _usable(Path(env).expanduser()):
        return Path(env).expanduser()
    found = [p for p in candidates() if _usable(p)]
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


def require_cli(explicit: str | os.PathLike | None = None) -> Path:
    """Like discover_cli, but raise with the places searched when nothing is found."""
    path = discover_cli(explicit)
    if path is None:
        raise FileNotFoundError(
            f"magda_cli not found: pass --cli/--engine, set ${ENV_VAR}, or build MAGDA under "
            f"{magda_root()} (see docs/BUILDING_MAGDA.md)")
    return path
