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


class RoutedMemory(MemoryInterface):
    """Each access through the first interface if it reaches the range, else the second.

    What :meth:`PlatformHarness.memory <soc_pastel.core.harness.PlatformHarness.memory>`
    returns on a platform with a backdoor: the backdoor where one reaches,
    on-chip debug elsewhere (e.g. a memory with no backdoor).
    """

    name = "memory"

    def __init__(self, preferred: MemoryInterface, fallback: MemoryInterface) -> None:
        self.preferred = preferred
        self.fallback = fallback

    def route(self, addr: int, length: int = 1) -> MemoryInterface:
        """The interface an access to ``[addr, addr + length)`` goes through."""
        return self.preferred if self.preferred.can_access(addr, length) else self.fallback

    def read(self, addr: int, length: int) -> bytes:  # noqa: D102
        return self.route(addr, length).read(addr, length)

    def write(self, addr: int, data: bytes) -> None:  # noqa: D102
        self.route(addr, len(data)).write(addr, data)

    def can_access(self, addr: int, length: int = 1) -> bool:  # noqa: D102
        return self.route(addr, length).can_access(addr, length)


class WordMemory(MemoryInterface):
    """Memory reached in aligned 32-bit words, e.g. through a debug probe.

    Byte-granular reads and writes are made of whole words, read-modify-writing
    partial words at the edges: some memories (VeeR's ICCM, for one) reject
    byte accesses over debug. Subclasses provide :meth:`_read_words` and
    :meth:`_write_words`.
    """

    name = "on_chip_debug"

    @abstractmethod
    def _read_words(self, addr: int, count: int) -> bytes:
        """Read ``count`` 32-bit words from word-aligned ``addr``, as bytes."""

    @abstractmethod
    def _write_words(self, addr: int, data: bytes) -> None:
        """Write whole 32-bit words (``len(data)`` a multiple of 4) at word-aligned ``addr``."""

    def read(self, addr: int, length: int) -> bytes:  # noqa: D102
        start, end = addr & ~3, (addr + length + 3) & ~3
        data = self._read_words(start, (end - start) // 4)
        return data[addr - start : addr - start + length]

    def write(self, addr: int, data: bytes) -> None:  # noqa: D102
        if not data:
            return
        start, end = addr & ~3, (addr + len(data) + 3) & ~3
        buf = bytearray(end - start)
        if addr != start:
            buf[0:4] = self._read_words(start, 1)
        if addr + len(data) != end:
            buf[-4:] = self._read_words(end - 4, 1)
        buf[addr - start : addr - start + len(data)] = data
        self._write_words(start, bytes(buf))
