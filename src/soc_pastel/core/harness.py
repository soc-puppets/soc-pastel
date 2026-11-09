"""The harness: what a test actually holds.

A :class:`PlatformHarness` bundles the device, the variant in use, the
platform, and the interfaces bound for that platform. Tests reach everything
through it, which is what lets the same test run on every platform.

Lifecycle: the harness opens the platform, then on-chip debug, then every
other interface; it closes them in reverse order. Opening on-chip debug first
means other interfaces may rely on it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar

from .device import ON_CHIP_DEBUG, Device, Variant
from .endpoints import EndpointBacked, ExpectedEndpoint
from .errors import CapabilityError
from .interface import Interface
from .memory import MemoryInterface
from .on_chip_debug import OnChipDebug
from .platform import Platform, SimulationPlatform

T = TypeVar("T", bound=Interface)


class PlatformHarness:
    """Everything a test needs to drive the device on the current platform.

    Args:
        device: The device descriptor.
        variant: The device build in use.
        platform: The platform the device runs on.
        interfaces: The interfaces bound for this platform, keyed by instance
            name, as ``(implementation class, binding config)`` pairs. The
            harness instantiates them. Must include ``"on_chip_debug"``.
        register_model: Factory for the device's register model, if it has
            one. Called once, lazily, with the open harness, so the model can
            bind to :meth:`memory`.
    """

    def __init__(
        self,
        device: Device,
        variant: Variant,
        platform: Platform,
        interfaces: Mapping[str, tuple[type[Interface], Mapping[str, Any]]],
        register_model: Callable[[PlatformHarness], Any] | None = None,
    ) -> None:
        self.device = device
        self.variant = variant
        self.platform = platform
        self._interfaces: dict[str, Interface] = {
            name: cls(name, cfg, self) for name, (cls, cfg) in interfaces.items()
        }
        self._register_model_factory = register_model
        self._register_model: Any | None = None
        self._open: list[Interface] = []
        if not isinstance(self._interfaces.get(ON_CHIP_DEBUG), OnChipDebug):
            raise CapabilityError(f"harness needs an {ON_CHIP_DEBUG!r} interface")

    # -- Capabilities ---------------------------------------------------------

    @property
    def capabilities(self) -> frozenset[str]:
        """Platform features plus the instance name of every bound interface."""
        return self.platform.capabilities() | frozenset(self._interfaces)

    def has(self, *capabilities: str) -> bool:
        """Return True if the harness offers every named capability."""
        return set(capabilities) <= self.capabilities

    def require(self, *capabilities: str) -> None:
        """Raise :class:`CapabilityError` unless every named capability is offered."""
        missing = sorted(set(capabilities) - self.capabilities)
        if missing:
            raise CapabilityError(
                f"{', '.join(missing)} not available on {self.platform.name} "
                f"({self.device.name}/{self.variant.name})"
            )

    # -- Interfaces -----------------------------------------------------------

    def iface(self, name: str, expected: type[T] = Interface) -> T:  # type: ignore[assignment]
        """Return the interface instance called ``name``.

        Args:
            name: Instance name from the device descriptor.
            expected: Optional contract class to check the instance against,
                e.g. ``harness.iface("spi0", SpiEndpoint)``.
        """
        self.require(name)
        inst = self._interfaces[name]
        if not isinstance(inst, expected):
            raise CapabilityError(f"{name!r} is a {inst.type_name}, not a {expected.__name__}")
        return inst

    @property
    def ocd(self) -> OnChipDebug:
        """The on-chip debug interface (shortcut for ``iface("on_chip_debug")``)."""
        return self.iface(ON_CHIP_DEBUG, OnChipDebug)

    def interfaces(self) -> Iterable[str]:
        """Instance names of every bound interface."""
        return self._interfaces.keys()

    # -- Memory and registers -------------------------------------------------

    def memory(self, *, prefer_backdoor: bool = True) -> MemoryInterface:
        """Return the best memory interface.

        The backdoor when the platform has one and ``prefer_backdoor`` is set,
        otherwise memory access through on-chip debug.
        """
        if prefer_backdoor:
            backdoor = self.platform.backdoor()
            if backdoor is not None:
                return backdoor
        return self.ocd.memory

    @property
    def regs(self) -> Any:
        """The device's register model. Raises CapabilityError if it has none."""
        if self._register_model_factory is None:
            raise CapabilityError(f"device {self.device.name!r} has no register model")
        if self._register_model is None:
            self._register_model = self._register_model_factory(self)
        return self._register_model

    # -- Lifecycle --------------------------------------------------------------

    def open(self) -> None:
        """Start the platform, then open on-chip debug and the other interfaces."""
        if isinstance(self.platform, SimulationPlatform):
            self.platform.expected_endpoints = {
                name: ExpectedEndpoint(type(inst).announced_type(), type(inst).handler_class)
                for name, inst in self._interfaces.items()
                if isinstance(inst, EndpointBacked)
            }
        self.platform.start()
        order = [ON_CHIP_DEBUG] + [n for n in self._interfaces if n != ON_CHIP_DEBUG]
        try:
            for name in order:
                inst = self._interfaces[name]
                inst.open()
                self._open.append(inst)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Close interfaces in reverse order, then stop the platform. Idempotent."""
        errors: list[BaseException] = []
        while self._open:
            try:
                self._open.pop().close()
            except Exception as e:  # keep closing the rest
                errors.append(e)
        try:
            self.platform.stop()
        except Exception as e:
            errors.append(e)
        if errors:
            raise errors[0]

    def __enter__(self) -> PlatformHarness:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
