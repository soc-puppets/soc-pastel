"""Base class for chip-boundary interfaces.

An *interface* is something on the chip's boundary that exists on every
platform in some form: on-chip debug, GPIO, SPI, UART. Each interface type
has a platform-neutral contract (an abstract subclass of :class:`Interface`)
and one implementation per way of reaching it (a simulation VIP, a bench
adapter, a probe).

Implementations are registered under the ``soc_pastel.interfaces`` entry-point
group as ``"<type>/<impl>"``, e.g. ``"spi/sim"`` or ``"on_chip_debug/jlink"``.
The device descriptor's :class:`~soc_pastel.core.device.InterfaceBinding`
chooses an implementation per platform.

Things that only some platforms have and that are not on the chip's pins
(backdoor memory access, simulation time, waves) are *platform features*,
not interfaces; see :mod:`soc_pastel.core.capabilities`.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from .harness import PlatformHarness


class Interface(ABC):
    """One instance of a chip-boundary interface on the current platform.

    Subclasses defining a *contract* set :attr:`type_name`; subclasses
    providing an *implementation* also set :attr:`impl_name`.

    Args:
        name: Instance name from the device descriptor, e.g. ``"spi0"``.
        config: The binding's config for this platform, including its ``"impl"``
            key. Implementations must reject unknown keys rather than ignore them.
        harness: The harness opening this interface. Implementations use it
            to reach the platform (for example its endpoint session).
    """

    type_name: ClassVar[str]
    impl_name: ClassVar[str]

    def __init__(self, name: str, config: Mapping[str, Any], harness: PlatformHarness) -> None:
        self.name = name
        self.config = config
        self.harness = harness

    def open(self) -> None:  # noqa: B027 - deliberately optional hook
        """Acquire resources. Called once by the harness before tests use the interface."""

    def close(self) -> None:  # noqa: B027 - deliberately optional hook
        """Release resources. Called once by the harness; must be idempotent."""

    def __repr__(self) -> str:
        impl = getattr(self, "impl_name", "?")
        return f"<{type(self).__name__} {self.name!r} ({self.type_name}/{impl})>"
