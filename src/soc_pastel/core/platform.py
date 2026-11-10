"""Platforms: where the device under test is running.

A platform is a simulator, an FPGA board or a silicon bench. Platforms are
registered under the ``soc_pastel.platforms`` entry-point group by name
(``"verilator"``, ``"xcelium"``, ``"fpga"``, ``"silicon"``, ...).

The split into :class:`SimulationPlatform` and :class:`HardwarePlatform`
reflects real differences rather than forcing hardware into a
simulation-shaped API (an earlier single ``Platform`` reported hardware as never
running, so timeouts never armed on silicon):

* A simulation platform builds and launches a process, talks to it over the
  endpoint protocol, and offers backdoor memory and simulation time.
* A hardware platform is already running; "starting" it means preparing it
  (e.g. programming an FPGA) and it is running once that succeeds.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from .capabilities import BACKDOOR, SIM_TIME

if TYPE_CHECKING:
    import pytest

    from .device import Device, Variant
    from .endpoints import EndpointHandler, EndpointSession, ExpectedEndpoint
    from .memory import MemoryInterface


class PlatformKind(str, Enum):
    """Broad class of platform; used as a selector in device descriptors."""

    SIM = "sim"
    FPGA = "fpga"
    SILICON = "silicon"


class PlatformImage(ABC):
    """A build artefact a platform runs: a simulation model, a bitstream.

    Images are built at most once per regression, even when tests run in
    parallel; implementations must make :meth:`build` safe to call from
    several processes at once.
    """

    @property
    @abstractmethod
    def key(self) -> str:
        """Identity of the image; equal keys mean interchangeable images."""

    @abstractmethod
    def is_built(self) -> bool:
        """Return True if the image exists and is up to date."""

    @abstractmethod
    def build(self) -> None:
        """Build the image if needed. Raises ConfigurationError on failure."""


class Platform(ABC):
    """Base class for all platforms.

    Args:
        device: The device descriptor.
        variant: The device build in use on this platform.
        options: Platform-specific options, from the command line and config.
    """

    #: Registered platform name, e.g. ``"verilator"``.
    name: ClassVar[str]
    #: Platform kind, used for descriptor selectors and variant choice.
    kind: ClassVar[PlatformKind]

    def __init__(self, device: Device, variant: Variant, options: Mapping[str, Any]) -> None:
        self.device = device
        self.variant = variant
        self.options = options

    @classmethod  # noqa: B027 - optional hook
    def add_options(cls, parser: pytest.Parser) -> None:
        """Add this platform's command-line options to pytest."""

    @abstractmethod
    def capabilities(self) -> frozenset[str]:
        """Platform features available (see :mod:`soc_pastel.core.capabilities`)."""

    @abstractmethod
    def start(self) -> None:
        """Bring the platform to the point where on-chip debug can connect."""

    @abstractmethod
    def stop(self) -> None:
        """Shut the platform down. Must be idempotent and safe after a failed start."""

    @property
    @abstractmethod
    def running(self) -> bool:
        """True while the platform can execute target code."""

    def time_ns(self) -> int | None:
        """Platform time in nanoseconds, or None if only wall-clock time exists."""
        return None

    #: Rough ratio of wall-clock time to target time, used to scale timeouts
    #: on platforms that do not report their own time. 1.0 for hardware.
    speed_factor: float = 1.0

    def backdoor(self) -> MemoryInterface | None:
        """Backdoor memory access, or None if the platform has none."""
        return None

    def set_waves(self, enable: bool) -> bool:
        """Turn waveform dumping on or off; return True if waves are now being dumped.

        Does nothing and returns False on a platform without the ``"waves"``
        capability, so tests and firmware may call it unconditionally. Tests
        that need waveforms should ``require`` the capability instead.
        """
        return False

    def debug_connection(self) -> Mapping[str, Any]:
        """How on-chip debug reaches the target on this platform.

        Simulations return e.g. ``{"remote_bitbang": ("127.0.0.1", 41234)}``;
        hardware platforms usually return nothing, because the debug binding
        names its probe directly.
        """
        return {}

    @property
    def run_dir(self) -> Path:
        """Directory for this run's logs and generated files."""
        path = Path(self.options.get("run_dir") or ".")
        path.mkdir(parents=True, exist_ok=True)
        return path


class SimulationPlatform(Platform):
    """A platform that runs a simulation model.

    The simulator dials back to the host over the endpoint protocol (see
    :mod:`soc_pastel.core.endpoints`); the platform owns that session and
    exposes the backdoor and platform-manager endpoints through it.

    Before :meth:`start`, the harness tells the platform which interface
    endpoints to expect (:attr:`expected_endpoints`). On HELLO the platform
    passes them, with its own :attr:`platform_handlers`, to
    :func:`~soc_pastel.core.endpoints.match_hello`.
    """

    kind: ClassVar[PlatformKind] = PlatformKind.SIM

    #: Handlers for the platform's own endpoints, keyed by ``"pastel.*"`` type.
    platform_handlers: ClassVar[Mapping[str, type[EndpointHandler]]] = {}

    def __init__(self, device: Device, variant: Variant, options: Mapping[str, Any]) -> None:
        super().__init__(device, variant, options)
        #: Endpoint-backed interfaces bound for this run, keyed by instance name.
        self.expected_endpoints: dict[str, ExpectedEndpoint] = {}

    def capabilities(self) -> frozenset[str]:
        """Simulations always have a backdoor and their own notion of time."""
        return frozenset({BACKDOOR, SIM_TIME})

    @abstractmethod
    def image(self) -> PlatformImage:
        """The simulation model for this device variant."""

    @property
    @abstractmethod
    def endpoints(self) -> EndpointSession:
        """The live endpoint session with the running simulator."""


class HardwarePlatform(Platform):
    """A platform running on physical hardware (FPGA board or silicon)."""

    def capabilities(self) -> frozenset[str]:
        """Hardware has no platform features by default."""
        return frozenset()

    def prepare(self) -> None:  # noqa: B027 - optional hook
        """Make the hardware ready, e.g. program an FPGA. Called by :meth:`start`."""
