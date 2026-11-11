"""What a device pack gives a simulator: the HDL to build.

A variant opts in to simulation by naming a hook in its params::

    params={"sim_sources": "my_pack.sim:sim_sources"}

The hook receives a work directory it may use for setup (e.g. a FuseSoC
build) and returns :class:`SimSources`. A hook that also takes a
``platform`` keyword argument is told which simulation platform is building
(``"verilator"``, ...), so one variant can serve several simulators::

    def sim_sources(workdir: Path, platform: str = "verilator") -> SimSources: ...
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

from soc_pastel.core.device import Variant
from soc_pastel.core.errors import ConfigurationError


@dataclass(frozen=True)
class SimSources:
    """HDL for a testbench.

    Attributes:
        top: Testbench top module. It has no ports and generates its own
            clock, as for any simulator.
        args: Simulator-independent source arguments: file paths,
            ``+incdir+<dir>`` and ``-D<define>[=<value>]``. Files ending in
            ``.vlt`` are Verilator configuration files and other simulators
            ignore them.
        key: Extra identity for build caching (e.g. an upstream commit).
        tool_args: Extra arguments for particular simulators, keyed by
            platform name, e.g. ``{"verilator": ("-CFLAGS", "-fcoroutines")}``.
            Each platform passes only its own, after PASTEL's options and
            before the sources.
    """

    top: str
    args: tuple[str, ...]
    key: str = ""
    tool_args: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def load_sim_sources(variant: Variant, workdir: Path, platform: str | None = None) -> SimSources:
    """Call the variant's ``sim_sources`` hook, telling it ``platform`` if it asks."""
    ref = variant.params.get("sim_sources")
    if not isinstance(ref, str) or ":" not in ref:
        raise ConfigurationError(
            f"variant {variant.name!r} has no 'sim_sources' param ('module:function')"
        )
    module, _, attr = ref.partition(":")
    hook: Callable[[Path], SimSources] = getattr(import_module(module), attr)
    workdir.mkdir(parents=True, exist_ok=True)
    params = inspect.signature(hook).parameters.values()
    if platform is not None and any(
        p.name == "platform" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params
    ):
        return hook(workdir, platform=platform)
    return hook(workdir)
