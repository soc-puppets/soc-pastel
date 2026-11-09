"""Access to target memory.

The API is byte-oriented on purpose: addresses and lengths are always in
bytes, and multi-byte values are little-endian unless stated otherwise.
(An earlier J-Link wrapper mixed up item sizes in bits and bytes, corrupting
multi-chunk writes; a single unit removes that class of bug.)

Implementations include the on-chip debug path (frontdoor, works everywhere,
slow in simulation) and the simulation backdoor (fast, simulation only).
:meth:`PlatformHarness.memory` picks one.
"""

from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from collections.abc import Sequence

_WORD = {1: "B", 2: "H", 4: "I", 8: "Q"}


class MemoryInterface(ABC):
    """Read and write target memory."""

    #: Human-readable name used in logs, e.g. ``"backdoor"`` or ``"on_chip_debug"``.
    name: str = "memory"

    @abstractmethod
    def read(self, addr: int, length: int) -> bytes:
        """Read ``length`` bytes starting at ``addr``.

        Raises:
            TargetError: The access failed or the range is not accessible
                through this interface.
        """

    @abstractmethod
    def write(self, addr: int, data: bytes) -> None:
        """Write ``data`` starting at ``addr``.

        Raises:
            TargetError: The access failed or the range is not accessible
                through this interface.
        """

    def can_access(self, addr: int, length: int = 1) -> bool:
        """Return True if this interface can reach ``[addr, addr + length)``.

        The default assumes it can; implementations restricted to some
        regions (e.g. the backdoor) override it.
        """
        return True

    # -- Convenience helpers; implementations need not override these. ------

    def read_words(self, addr: int, count: int, width: int = 4) -> list[int]:
        """Read ``count`` little-endian words of ``width`` bytes each."""
        fmt = "<" + _WORD[width] * count
        return list(struct.unpack(fmt, self.read(addr, count * width)))

    def write_words(self, addr: int, values: Sequence[int], width: int = 4) -> None:
        """Write little-endian words of ``width`` bytes each."""
        fmt = "<" + _WORD[width] * len(values)
        self.write(addr, struct.pack(fmt, *values))

    def read32(self, addr: int) -> int:
        """Read one 32-bit little-endian word."""
        return self.read_words(addr, 1, 4)[0]

    def write32(self, addr: int, value: int) -> None:
        """Write one 32-bit little-endian word."""
        self.write_words(addr, [value], 4)
