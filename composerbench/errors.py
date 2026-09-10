"""Structured, actionable errors (spec §21, §84).

Every error must be machine-readable:
    {"error": "PARAMETER_OUT_OF_RANGE", "device": ..., "parameter": ..., ...}
Never bare codes like "Error -4".
"""

from __future__ import annotations

from typing import Any


class ComposerBenchError(Exception):
    """Base class for all structured ComposerBench errors."""

    code: str = "COMPOSERBENCH_ERROR"

    def __init__(self, **details: Any) -> None:
        self.details: dict[str, Any] = details
        parts = ", ".join(f"{k}={v!r}" for k, v in details.items())
        super().__init__(f"{self.code}: {parts}" if parts else self.code)

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, **self.details}


class UnsupportedOperation(ComposerBenchError):
    code = "UNSUPPORTED_OPERATION"


class EntityNotFound(ComposerBenchError):
    code = "ENTITY_NOT_FOUND"

    def __init__(self, kind: str, requested: str, available_matches: list[str] | None = None):
        super().__init__(
            kind=kind,
            requested=requested,
            available_matches=available_matches or [],
        )


class ParameterOutOfRange(ComposerBenchError):
    code = "PARAMETER_OUT_OF_RANGE"


class StaleState(ComposerBenchError):
    code = "STALE_STATE"


class RenderFailed(ComposerBenchError):
    code = "RENDER_FAILED"


class ConnectionLost(ComposerBenchError):
    code = "CONNECTION_LOST"


class MalformedDump(ComposerBenchError):
    code = "MALFORMED_DUMP"
