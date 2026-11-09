"""Running test firmware and collecting its result."""

from __future__ import annotations

import logging
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

from soc_pastel.core.errors import PastelTimeout, ProtocolError, TargetError
from soc_pastel.core.loader import elf_symbol, load_elf, memory_for
from soc_pastel.core.rtt import Rtt

if TYPE_CHECKING:
    from soc_pastel.core.harness import PlatformHarness

log = logging.getLogger("soc_pastel.firmware")

MAILBOX_VERSION = 1
TEXT_CHANNEL, MAILBOX_CHANNEL = 0, 1


class Mbox(IntEnum):
    """Mailbox message ids (mirrors fw/include/pastel/mailbox.h)."""

    HELLO = 0x01
    CHECKPOINT = 0x02
    DATA = 0x03
    FINISH = 0x04
    FAULT = 0x05
    HOST_DATA = 0x81
    HOST_ACK = 0x82


@dataclass
class FirmwareResult:
    """What a firmware run produced.

    Attributes:
        result: ``test_main``'s return value; 0 means pass.
        output: Everything the firmware printed.
        checkpoints: Checkpoint ids in the order reported.
        data: Payloads of ``pastel_send`` calls, in order.
    """

    result: int
    output: str = ""
    checkpoints: list[int] = field(default_factory=list)
    data: list[bytes] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if ``test_main`` returned 0."""
        return self.result == 0


class FirmwareFault(TargetError):
    """The firmware took an unexpected exception."""

    def __init__(self, cause: int, pc: int, tval: int, output: str) -> None:
        super().__init__(f"firmware fault: mcause={cause:#x} mepc={pc:#010x} mtval={tval:#x}")
        self.cause, self.pc, self.tval, self.output = cause, pc, tval, output


def run_firmware(
    harness: PlatformHarness,
    elf: Path,
    *,
    timeout_s: float = 300.0,
    on_checkpoint: Callable[[int], None] | None = None,
    poll_s: float = 0.005,
) -> FirmwareResult:
    """Reset the target, load and run ``elf``, and wait for it to finish.

    Checkpoints sent with ``wait`` set are acknowledged after ``on_checkpoint``
    returns, so host code can act at a known point in the firmware.

    Raises:
        FirmwareFault: The firmware took an unexpected exception.
        PastelTimeout: It did not finish within ``timeout_s`` wall-clock seconds.
    """
    ocd = harness.ocd
    ocd.reset(halt=True)
    entry = load_elf(harness, elf)
    cb = elf_symbol(elf, "_SEGGER_RTT")
    rtt = Rtt(memory_for(harness, cb, 64), cb)
    ocd.resume(entry)

    deadline = time.monotonic() + timeout_s
    rtt.wait_ready(timeout_s)
    result = FirmwareResult(result=-1)
    text, line, mbox = [], bytearray(), bytearray()
    while True:
        chunk = rtt.read_up(TEXT_CHANNEL)
        if chunk:
            text.append(chunk.decode(errors="replace"))
            line += chunk
            *done, rest = line.split(b"\n")
            for ln in done:
                log.info("fw: %s", ln.decode(errors="replace"))
            line = bytearray(rest)
        mbox += rtt.read_up(MAILBOX_CHANNEL)
        while len(mbox) >= 2 and len(mbox) >= 2 + mbox[1]:
            mid, n = mbox[0], mbox[1]
            payload = bytes(mbox[2 : 2 + n])
            del mbox[: 2 + n]
            result.output = "".join(text)
            if mid == Mbox.HELLO:
                if payload[:1] != bytes([MAILBOX_VERSION]):
                    raise ProtocolError(f"firmware mailbox version {payload[:1]!r} unsupported")
            elif mid == Mbox.CHECKPOINT:
                (cp,) = struct.unpack("<I", payload)
                result.checkpoints.append(cp)
                if on_checkpoint:
                    on_checkpoint(cp)
                ack = bytes([Mbox.HOST_ACK, 4]) + payload
                while ack:
                    ack = ack[rtt.write_down(MAILBOX_CHANNEL, ack) :]
            elif mid == Mbox.DATA:
                result.data.append(payload)
            elif mid == Mbox.FINISH:
                (result.result,) = struct.unpack("<i", payload)
                return result
            elif mid == Mbox.FAULT:
                raise FirmwareFault(*struct.unpack("<III", payload), result.output)
        if time.monotonic() > deadline:
            raise PastelTimeout(f"firmware did not finish within {timeout_s:.0f} s")
        if not harness.platform.running:
            raise TargetError("platform stopped while firmware was running")
        time.sleep(poll_s)
