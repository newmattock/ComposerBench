"""Mix and master stage: stems + a JSON spec -> finished master and report."""
from composerbench.mixing.spec import MixSpec, example_spec, load_mix_spec

__all__ = ["MixSpec", "example_spec", "load_mix_spec", "run_mix"]


def __getattr__(name):
    if name == "run_mix":
        from composerbench.mixing.mixdown import run_mix
        return run_mix
    raise AttributeError(name)
