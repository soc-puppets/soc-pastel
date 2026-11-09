"""Discovery of installed devices, platforms, interfaces and HDL.

Everything pluggable is found through entry points, so first-party code and
third-party packs are discovered the same way.

==========================  ==============================  =======================
Group                       Entry-point name                Value
==========================  ==============================  =======================
``soc_pastel.devices``      device name                     a ``Device`` instance
``soc_pastel.platforms``    platform name                   a ``Platform`` subclass
``soc_pastel.interfaces``   ``"<type>/<impl>"``             an ``Interface`` subclass
``soc_pastel.hdl``          pack name                       a ``Path`` to FuseSoC cores
==========================  ==============================  =======================

Host-side endpoint handlers are not registered separately: a simulation
implementation of an interface names its handler (see
:class:`~soc_pastel.core.endpoints.EndpointBacked`), and the simulation
platform supplies handlers for its own endpoints.
"""

from __future__ import annotations

from functools import cache
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any

from .device import Device
from .errors import ConfigurationError
from .interface import Interface
from .platform import Platform

DEVICES = "soc_pastel.devices"
PLATFORMS = "soc_pastel.platforms"
INTERFACES = "soc_pastel.interfaces"
HDL = "soc_pastel.hdl"


def _group(group: str) -> dict[str, EntryPoint]:
    found: dict[str, EntryPoint] = {}
    for ep in entry_points(group=group):
        if ep.name in found and found[ep.name].value != ep.value:
            raise ConfigurationError(
                f"{group}: {ep.name!r} registered twice ({found[ep.name].value}, {ep.value})"
            )
        found[ep.name] = ep
    return found


def _load(group: str, name: str, kind: str) -> Any:
    eps = _group(group)
    if name not in eps:
        known = ", ".join(sorted(eps)) or "none installed"
        raise ConfigurationError(f"no {kind} {name!r} (known: {known})")
    return eps[name].load()


def device_names() -> list[str]:
    """Names of every installed device."""
    return sorted(_group(DEVICES))


@cache
def device(name: str) -> Device:
    """Load the installed device called ``name``."""
    obj = _load(DEVICES, name, "device")
    if not isinstance(obj, Device):
        raise ConfigurationError(f"device entry point {name!r} is not a Device")
    return obj


def platform_names() -> list[str]:
    """Names of every installed platform."""
    return sorted(_group(PLATFORMS))


def platform(name: str) -> type[Platform]:
    """Load the platform class called ``name``."""
    cls = _load(PLATFORMS, name, "platform")
    if not (isinstance(cls, type) and issubclass(cls, Platform)):
        raise ConfigurationError(f"platform entry point {name!r} is not a Platform subclass")
    return cls


def interface(type_name: str, impl: str) -> type[Interface]:
    """Load the implementation ``impl`` of interface type ``type_name``."""
    cls = _load(INTERFACES, f"{type_name}/{impl}", "interface implementation")
    if not (isinstance(cls, type) and issubclass(cls, Interface)):
        raise ConfigurationError(f"{type_name}/{impl} is not an Interface subclass")
    if cls.type_name != type_name:
        raise ConfigurationError(f"{type_name}/{impl} implements {cls.type_name!r}")
    return cls


def hdl_roots() -> dict[str, Path]:
    """Directories containing FuseSoC cores, keyed by pack name."""
    roots = {}
    for name, ep in _group(HDL).items():
        path = Path(ep.load())
        if not path.is_dir():
            raise ConfigurationError(f"HDL root for {name!r} does not exist: {path}")
        roots[name] = path
    return roots
