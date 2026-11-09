"""Host side of RTT (SEGGER-compatible control block layout).

The host reads target memory directly, so RTT works through whatever memory
interface reaches the control block: the backdoor in simulation, on-chip
debug on hardware.
"""

from __future__ import annotations

import struct
import time

from .errors import PastelTimeout, TargetError
from .memory import MemoryInterface

_ID = b"SEGGER RTT"
_HEADER = 24  # id[16], max_up, max_down
_DESC = 24  # name, buf, size, wr, rd, flags


class Rtt:
    """An RTT control block in target memory."""

    def __init__(self, memory: MemoryInterface, address: int) -> None:
        self.memory = memory
        self.address = address
        self.max_up = self.max_down = 0

    def wait_ready(self, timeout_s: float) -> None:
        """Wait until the firmware has initialised the control block."""
        deadline = time.monotonic() + timeout_s
        while self.memory.read(self.address, len(_ID)) != _ID:
            if time.monotonic() > deadline:
                raise PastelTimeout("RTT control block was not initialised in time")
            time.sleep(0.01)
        self.max_up, self.max_down = struct.unpack("<ii", self.memory.read(self.address + 16, 8))

    def _desc(self, up: bool, channel: int) -> int:
        limit = self.max_up if up else self.max_down
        if not 0 <= channel < limit:
            raise TargetError(f"RTT has no {'up' if up else 'down'} channel {channel}")
        base = self.address + _HEADER + (0 if up else _DESC * self.max_up)
        return base + _DESC * channel

    def read_up(self, channel: int) -> bytes:
        """Read and consume everything pending on an up (target-to-host) channel."""
        d = self._desc(True, channel)
        _, buf, size, wr, rd, _ = struct.unpack("<6I", self.memory.read(d, _DESC))
        if wr == rd or wr >= size or rd >= size:
            return b""
        if wr > rd:
            data = self.memory.read(buf + rd, wr - rd)
        else:
            data = self.memory.read(buf + rd, size - rd) + (
                self.memory.read(buf, wr) if wr else b""
            )
        self.memory.write(d + 16, struct.pack("<I", wr))
        return data

    def write_down(self, channel: int, data: bytes) -> int:
        """Write as much of ``data`` as fits on a down channel; return bytes written."""
        d = self._desc(False, channel)
        _, buf, size, wr, rd, _ = struct.unpack("<6I", self.memory.read(d, _DESC))
        space = (rd - wr - 1) % size
        data = data[:space]
        first = min(len(data), size - wr)
        if first:
            self.memory.write(buf + wr, data[:first])
        if len(data) > first:
            self.memory.write(buf, data[first:])
        self.memory.write(d + 12, struct.pack("<I", (wr + len(data)) % size))
        return len(data)
