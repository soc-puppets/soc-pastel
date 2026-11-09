"""Handlers for the simulation platform's own endpoints."""

from __future__ import annotations

import struct
import threading

from soc_pastel.core.device import Variant
from soc_pastel.core.endpoints import EndpointHandler, EndpointInfo
from soc_pastel.core.errors import PastelTimeout, ProtocolError, TargetError
from soc_pastel.core.memory import MemoryInterface

_REPLY_TIMEOUT_S = 60.0


class _RequestReply(EndpointHandler):
    """Base for endpoints answering requests tagged with a sequence number."""

    def __init__(self, info: EndpointInfo, send) -> None:
        super().__init__(info, send)
        self._cv = threading.Condition()
        self._replies: dict[int, bytes] = {}
        self._seq = 0
        self._dead = False

    def _next_seq(self) -> int:
        with self._cv:
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            return self._seq

    def _deliver(self, seq: int, payload: bytes) -> None:
        with self._cv:
            self._replies[seq] = payload
            self._cv.notify_all()

    def _wait(self, seq: int, what: str) -> bytes:
        with self._cv:
            ok = self._cv.wait_for(lambda: seq in self._replies or self._dead, _REPLY_TIMEOUT_S)
            if seq in self._replies:
                return self._replies.pop(seq)
        if not ok:
            raise PastelTimeout(f"no reply from {self.info.name} to {what}")
        raise ProtocolError(f"simulator closed while waiting for {what}")

    def on_close(self) -> None:  # noqa: D102
        with self._cv:
            self._dead = True
            self._cv.notify_all()


class PlatformControl(_RequestReply):
    """The ``pastel.platform`` endpoint: finish and simulation time."""

    type_name = "pastel.platform"
    _FINISH, _GET_TIME, _TIME = 0x01, 0x02, 0x82

    def on_message(self, payload: bytes) -> None:  # noqa: D102
        if payload[:1] == bytes([self._TIME]) and len(payload) == 9:
            self._deliver(0, payload[1:])

    def request_finish(self) -> None:
        """Ask the simulator to finish."""
        self.send(bytes([self._FINISH]))

    def time_ns(self) -> int:
        """Current simulation time in nanoseconds."""
        self.send(bytes([self._GET_TIME]))
        return int.from_bytes(self._wait(0, "time request"), "little")


class Backdoor(_RequestReply):
    """One ``pastel.backdoor`` endpoint: byte access to one memory."""

    type_name = "pastel.backdoor"
    READ, WRITE = 1, 2
    _HDR = struct.Struct("<BIII")

    def on_message(self, payload: bytes) -> None:  # noqa: D102
        if len(payload) >= 6:
            self._deliver(int.from_bytes(payload[1:5], "little"), payload)

    def access(self, op: int, offset: int, length: int, data: bytes = b"") -> bytes:
        """Perform one request; return read data (empty for writes)."""
        seq = self._next_seq()
        self.send(self._HDR.pack(op, seq, offset, length) + data)
        reply = self._wait(seq, f"backdoor {'read' if op == self.READ else 'write'}")
        if reply[5] != 0:
            raise TargetError(
                f"backdoor {self.info.name!r}: offset {offset:#x}+{length} is out of range"
            )
        return reply[6:]


class BackdoorMemory(MemoryInterface):
    """Memory access through the simulation's backdoor endpoints.

    Addresses are routed to the region containing them; the region's
    ``backdoor`` name selects the endpoint.
    """

    name = "backdoor"
    CHUNK = 4096

    def __init__(self, variant: Variant, endpoints: dict[str, Backdoor]) -> None:
        self._variant = variant
        self._endpoints = endpoints

    def _route(self, addr: int, length: int) -> tuple[Backdoor, int]:
        region = self._variant.region_at(addr, max(length, 1))
        if region is None or region.backdoor is None or region.backdoor not in self._endpoints:
            raise TargetError(f"no backdoor reaches {addr:#x}+{length}")
        return self._endpoints[region.backdoor], addr - region.base

    def can_access(self, addr: int, length: int = 1) -> bool:  # noqa: D102
        region = self._variant.region_at(addr, max(length, 1))
        return region is not None and region.backdoor in self._endpoints

    def read(self, addr: int, length: int) -> bytes:  # noqa: D102
        ep, off = self._route(addr, length)
        out = bytearray()
        for start in range(0, length, self.CHUNK):
            n = min(self.CHUNK, length - start)
            out += ep.access(Backdoor.READ, off + start, n)
        return bytes(out)

    def write(self, addr: int, data: bytes) -> None:  # noqa: D102
        ep, off = self._route(addr, len(data))
        for start in range(0, len(data), self.CHUNK):
            chunk = data[start : start + self.CHUNK]
            ep.access(Backdoor.WRITE, off + start, len(chunk), chunk)
