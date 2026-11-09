"""What a device pack gives a simulator: the HDL to build.

A variant opts in to simulation by naming a hook in its params::

    params={"sim_sources": "my_pack.sim:sim_sources"}

The hook receives a work directory it may use for setup (e.g. a FuseSoC
build) and returns :class:`SimSources`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

from soc_pastel.core.device import Variant
from soc_pastel.core.errors import ConfigurationError


@dataclass(frozen=True)
class SimSources:
    """HDL for a testbench.

    Attributes:
        top: Testbench top module. It must have a single input, ``clk``.
        args: Simulator-independent source arguments: file paths,
            ``+incdir+<dir>`` and ``-D<define>[=<value>]``. Files ending in
            ``.vlt`` are Verilator configuration files and other simulators
            ignore them.
        key: Extra identity for build caching (e.g. an upstream commit).
    """

    top: str
    args: tuple[str, ...]
    key: str = ""


def load_sim_sources(variant: Variant, workdir: Path) -> SimSources:
    """Call the variant's ``sim_sources`` hook."""
    ref = variant.params.get("sim_sources")
    if not isinstance(ref, str) or ":" not in ref:
        raise ConfigurationError(
            f"variant {variant.name!r} has no 'sim_sources' param ('module:function')"
        )
    module, _, attr = ref.partition(":")
    hook: Callable[[Path], SimSources] = getattr(import_module(module), attr)
    workdir.mkdir(parents=True, exist_ok=True)
    return hook(workdir)
