"""Adapter layer: control surfaces over workstations."""

from composerbench.adapter.base import AdapterCapabilities, DawAdapter, RenderRequest
from composerbench.adapter.fake import FakeDawAdapter

__all__ = [
    "AdapterCapabilities",
    "DawAdapter",
    "FakeDawAdapter",
    "RenderRequest",
]
