"""Declarative song building: spec -> MAGDA project -> mix + stem renders."""
from composerbench.song.spec import DeviceSpec, RenderSpec, SongSpec, TrackSpec, load_spec, resolve

__all__ = [
    "DeviceSpec",
    "RenderSpec",
    "SongSpec",
    "TrackSpec",
    "build_song",
    "check_song",
    "load_spec",
    "resolve",
]


def __getattr__(name):           # lazy: build pulls in the adapter stack
    if name in ("build_song", "check_song"):
        from composerbench.song import build
        return getattr(build, name)
    raise AttributeError(name)
