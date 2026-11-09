"""Device descriptors: everything PASTEL needs to know about one chip.

A device pack provides exactly one :class:`Device` per chip and registers it
under the ``soc_pastel.devices`` entry-point group. The core reads nothing
chip-specific from anywhere else.

Design notes
------------
* **Variants.** The same chip is often built differently for different
  platforms: VeeRwolf's simulation build enables 64 KiB ICCM/DCCM while its
  FPGA builds disable them, and an ASIC's FPGA prototype may have different
  RAM sizes. A :class:`Variant` captures one build: its memory regions, where
  firmware may be linked, and build parameters. The memory map lives here and
  only here; linker-script fragments and C headers are generated from it.
* **Interface bindings are declarative.** A binding names an implementation
  and its configuration per platform; the implementation itself need not be
  installed for the descriptor to load. It is resolved when the harness opens.
* **Regions describe behaviour, not CPU features.** ``side_effects`` marks
  I/O semantics; a CPU port translates that into its own mechanism (VeeR's
  MRAC, a Cortex-M MPU, ...).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum, Flag, auto
from types import MappingProxyType
from typing import Any

from .errors import ConfigurationError

#: Name every device must use for its on-chip debug interface binding.
ON_CHIP_DEBUG = "on_chip_debug"


class Access(Flag):
    """What the CPU may do with a memory region."""

    R = auto()
    W = auto()
    X = auto()
    RW = R | W
    RX = R | X
    RWX = R | W | X


class RegionKind(Enum):
    """Broad class of a memory region."""

    RAM = "ram"
    ROM = "rom"
    MMIO = "mmio"


@dataclass(frozen=True, kw_only=True)
class MemoryRegion:
    """A contiguous region of the target's address map.

    Attributes:
        name: Unique (within a variant) region name, e.g. ``"ram0"``, ``"dccm"``.
        base: First byte address.
        size: Size in bytes.
        kind: RAM, ROM or memory-mapped I/O.
        access: What the CPU may do with the region. Execute-only memories
            (e.g. a boot ROM not wired to the data port) are ``Access.X``.
        side_effects: True if accesses have I/O semantics and must not be
            cached, merged or speculated. Defaults to True for ``MMIO``.
        backdoor: Name under which the simulation backdoor endpoint exposes
            this region, or ``None`` if it cannot be accessed by backdoor.
            How banks and interleaving map onto storage is the testbench's
            business, not the descriptor's.
        debug_access: True if the on-chip debug interface can read and write
            the region (needed to load code into it without a backdoor).
    """

    name: str
    base: int
    size: int
    kind: RegionKind = RegionKind.RAM
    access: Access = Access.RWX
    side_effects: bool | None = None
    backdoor: str | None = None
    debug_access: bool = True

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ConfigurationError(f"region {self.name!r}: size must be positive")
        if self.base < 0 or self.end > 1 << 64:
            raise ConfigurationError(f"region {self.name!r}: address out of range")
        if self.side_effects is None:
            object.__setattr__(self, "side_effects", self.kind is RegionKind.MMIO)

    @property
    def end(self) -> int:
        """One past the last byte address."""
        return self.base + self.size

    def contains(self, addr: int, length: int = 1) -> bool:
        """Return True if ``[addr, addr + length)`` lies wholly within the region."""
        return self.base <= addr and addr + length <= self.end

    def overlaps(self, other: MemoryRegion) -> bool:
        """Return True if this region shares any address with ``other``."""
        return self.base < other.end and other.base < self.end


@dataclass(frozen=True, kw_only=True)
class CodeLocation:
    """A place firmware can be linked to run from.

    Tests may be parametrised over code locations (e.g. run from SRAM and from
    tightly-coupled memories). The firmware build generates a linker script
    from the regions named here.

    Attributes:
        name: Location name used on the command line and in test ids,
            e.g. ``"sram"``, ``"ccm"``.
        text: Region holding code and read-only data. Must be executable.
        data: Region holding initialised data, BSS and stack. Must be writable.
        rtt: Region holding the RTT control block; defaults to ``data``.
    """

    name: str
    text: str
    data: str
    rtt: str | None = None

    @property
    def rtt_region(self) -> str:
        """Region holding the RTT control block."""
        return self.rtt or self.data


@dataclass(frozen=True, kw_only=True)
class Variant:
    """One build of a device, with its own memory map.

    Attributes:
        name: Variant name, e.g. ``"sim"``, ``"fpga"``, ``"asic"``.
        regions: Every memory region the build has.
        code_locations: Where firmware may be linked, preferred first.
        platforms: Platform kinds or names this build is used on, e.g.
            ``{"sim"}`` or ``{"fpga"}``. Used to choose a default variant when
            none is given explicitly.
        params: Free-form build parameters the device pack's platform
            integration understands (FuseSoC flags, bitstream names, ...).
            The core never interprets them.
    """

    name: str
    regions: tuple[MemoryRegion, ...]
    code_locations: tuple[CodeLocation, ...]
    platforms: frozenset[str] = frozenset()
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "regions", tuple(self.regions))
        object.__setattr__(self, "code_locations", tuple(self.code_locations))
        object.__setattr__(self, "platforms", frozenset(self.platforms))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    def region(self, name: str) -> MemoryRegion:
        """Return the region called ``name``."""
        for r in self.regions:
            if r.name == name:
                return r
        raise ConfigurationError(f"variant {self.name!r} has no region {name!r}")

    def region_at(self, addr: int, length: int = 1) -> MemoryRegion | None:
        """Return the region containing ``[addr, addr + length)``, if any."""
        return next((r for r in self.regions if r.contains(addr, length)), None)

    def code_location(self, name: str) -> CodeLocation:
        """Return the code location called ``name``."""
        for c in self.code_locations:
            if c.name == name:
                return c
        raise ConfigurationError(f"variant {self.name!r} has no code location {name!r}")

    def validate(self) -> None:
        """Check internal consistency; raise :class:`ConfigurationError` if broken."""
        _unique((r.name for r in self.regions), f"variant {self.name!r} region")
        _unique((c.name for c in self.code_locations), f"variant {self.name!r} code location")
        regions = sorted(self.regions, key=lambda r: r.base)
        for a, b in zip(regions, regions[1:], strict=False):
            if a.overlaps(b):
                raise ConfigurationError(
                    f"variant {self.name!r}: regions {a.name!r} and {b.name!r} overlap"
                )
        if not self.code_locations:
            raise ConfigurationError(f"variant {self.name!r} declares no code locations")
        for c in self.code_locations:
            text, data, rtt = (self.region(n) for n in (c.text, c.data, c.rtt_region))
            if Access.X not in text.access:
                raise ConfigurationError(
                    f"code location {c.name!r}: text region {text.name!r} is not executable"
                )
            for r in (data, rtt):
                if Access.RW not in r.access:
                    raise ConfigurationError(
                        f"code location {c.name!r}: region {r.name!r} is not read/write"
                    )


@dataclass(frozen=True, kw_only=True)
class CpuSpec:
    """The CPU the firmware runs on.

    Attributes:
        family: CPU port name. Selects the firmware runtime port
            (``fw/ports/<family>``) and the OpenOCD CPU fragment.
        isa: ``-march`` string for the firmware toolchain.
        abi: ``-mabi`` string for the firmware toolchain.
        reset_vector: Address the CPU fetches from after reset.
        params: Port-specific parameters (e.g. VeeR PIC base address).
    """

    family: str
    isa: str
    abi: str
    reset_vector: int
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, kw_only=True)
class InterfaceBinding:
    """How one chip-boundary interface is reached on each platform.

    Attributes:
        type: Interface type, matching an implementation's ``type_name``
            (``"on_chip_debug"``, ``"gpio"``, ``"spi"``, ``"uart"``, ...).
        impls: Maps a *selector* to the implementation config for it. Each
            config must contain ``"impl"`` (the implementation name registered
            as ``"<type>/<impl>"``); everything else is passed to the
            implementation untouched. Selectors are tried most specific
            first: variant name, then platform name, then platform kind.
            A missing entry means the interface is absent on that platform.
    """

    type: str
    impls: Mapping[str, Mapping[str, Any]]

    def __post_init__(self) -> None:
        frozen = {k: MappingProxyType(dict(v)) for k, v in self.impls.items()}
        object.__setattr__(self, "impls", MappingProxyType(frozen))

    def resolve(self, *, variant: str, platform: str, kind: str) -> Mapping[str, Any] | None:
        """Return the config for the given variant/platform, or None if absent."""
        for selector in (variant, platform, kind):
            if selector in self.impls:
                return self.impls[selector]
        return None

    def validate(self, name: str) -> None:
        """Check every config names an implementation."""
        for selector, cfg in self.impls.items():
            if not isinstance(cfg.get("impl"), str):
                raise ConfigurationError(
                    f"interface {name!r}, selector {selector!r}: config needs an 'impl' string"
                )


@dataclass(frozen=True, kw_only=True)
class Device:
    """A chip, as PASTEL sees it.

    Attributes:
        name: Device name used with ``--device``.
        cpu: The CPU firmware runs on.
        variants: Builds of the device, keyed by name.
        default_variant: Variant used when neither the user nor the platform
            selects one.
        interfaces: Chip-boundary interfaces keyed by *instance* name
            (``"on_chip_debug"``, ``"spi0"``, ...). An ``on_chip_debug`` binding
            is mandatory.
        register_model: Optional ``"module:attribute"`` reference to a
            register-model factory. Devices without one simply lack ``regs``.
        hdl: FuseSoC VLNV of the device pack's testbench top, if it has one.
    """

    name: str
    cpu: CpuSpec
    variants: Mapping[str, Variant]
    default_variant: str
    interfaces: Mapping[str, InterfaceBinding]
    register_model: str | None = None
    hdl: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "variants", MappingProxyType(dict(self.variants)))
        object.__setattr__(self, "interfaces", MappingProxyType(dict(self.interfaces)))
        self.validate()

    def variant(self, name: str | None = None) -> Variant:
        """Return the named variant, or the default variant."""
        key = name or self.default_variant
        try:
            return self.variants[key]
        except KeyError:
            known = ", ".join(sorted(self.variants))
            raise ConfigurationError(
                f"device {self.name!r} has no variant {key!r} (known: {known})"
            ) from None

    def variant_for(self, *, platform: str, kind: str, requested: str | None = None) -> Variant:
        """Choose the variant to use on a platform.

        An explicit request wins. Otherwise the unique variant listing the
        platform name, then the unique variant listing the platform kind,
        then the default variant.
        """
        if requested:
            return self.variant(requested)
        for selector in (platform, kind):
            matches = [v for v in self.variants.values() if selector in v.platforms]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                names = ", ".join(sorted(v.name for v in matches))
                raise ConfigurationError(
                    f"device {self.name!r}: several variants suit {selector!r} ({names}); "
                    "choose one with --variant"
                )
        return self.variant()

    def validate(self) -> None:
        """Check internal consistency; raise :class:`ConfigurationError` if broken."""
        if self.default_variant not in self.variants:
            raise ConfigurationError(
                f"device {self.name!r}: default variant {self.default_variant!r} not defined"
            )
        for key, v in self.variants.items():
            if key != v.name:
                raise ConfigurationError(f"device {self.name!r}: variant key {key!r} != {v.name!r}")
            v.validate()
        dbg = self.interfaces.get(ON_CHIP_DEBUG)
        if dbg is None or dbg.type != ON_CHIP_DEBUG:
            raise ConfigurationError(
                f"device {self.name!r} must bind an {ON_CHIP_DEBUG!r} interface "
                f"of type {ON_CHIP_DEBUG!r}"
            )
        for name, binding in self.interfaces.items():
            binding.validate(name)


def _unique(names: Iterable[str], what: str) -> None:
    seen: set[str] = set()
    for n in names:
        if n in seen:
            raise ConfigurationError(f"duplicate {what} name {n!r}")
        seen.add(n)
