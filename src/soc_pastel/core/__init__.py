"""Chip-agnostic core of PASTEL.

Nothing in this package may refer to a specific chip, CPU, simulator or probe.
Everything chip-specific arrives through a :class:`~soc_pastel.core.device.Device`
descriptor; everything platform- or tool-specific arrives through the
entry-point groups listed in :mod:`soc_pastel.core.registry`.
"""

from .capabilities import BACKDOOR, SIM_TIME, WAVES
from .device import (
    Access,
    CodeLocation,
    CpuSpec,
    Device,
    InterfaceBinding,
    MemoryRegion,
    RegionKind,
    Variant,
)
from .endpoints import EndpointBacked, EndpointHandler, ExpectedEndpoint, match_hello
from .errors import (
    CapabilityError,
    ConfigurationError,
    PastelError,
    PastelTimeout,
    ProtocolError,
    TargetError,
)
from .harness import PlatformHarness
from .interface import Interface
from .memory import MemoryInterface
from .on_chip_debug import OnChipDebug, TargetState
from .platform import (
    HardwarePlatform,
    Platform,
    PlatformImage,
    PlatformKind,
    SimulationPlatform,
)

__all__ = [
    "BACKDOOR",
    "SIM_TIME",
    "WAVES",
    "Access",
    "CapabilityError",
    "CodeLocation",
    "ConfigurationError",
    "CpuSpec",
    "Device",
    "EndpointBacked",
    "EndpointHandler",
    "ExpectedEndpoint",
    "HardwarePlatform",
    "Interface",
    "InterfaceBinding",
    "MemoryInterface",
    "MemoryRegion",
    "OnChipDebug",
    "PastelError",
    "PastelTimeout",
    "Platform",
    "PlatformHarness",
    "PlatformImage",
    "PlatformKind",
    "ProtocolError",
    "RegionKind",
    "SimulationPlatform",
    "TargetError",
    "TargetState",
    "Variant",
    "match_hello",
]
