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

from soc_pastel.core.util import XERROR  # noqa: E402 - re-exported

MAILBOX_VERSION = 2
TEXT_CHANNEL, MAILBOX_CHANNEL = 0, 1


class Mbox(IntEnum):
    """Mailbox message ids (mirrors fw/include/pastel/mailbox.h)."""

    HELLO = 0x01
    CHECKPOINT = 0x02
    DATA = 0x03
    FINISH = 0x04
    FAULT = 0x05
    WAVES = 0x06
    ERROR = 0x07
    HOST_DATA = 0x81
    HOST_ACK = 0x82
    HOST_WAVES = 0x83


class HostLink:
    """Sends data to running firmware, which takes it with ``pastel_receive()``.

    :func:`run_firmware` connects it while the firmware runs; the ``firmware``
    fixture's :meth:`send` and :meth:`send_u32` use one. Data is sent at
    once, so it is waiting when the firmware next asks.
    """

    def __init__(self) -> None:
        self._send: Callable[[Mbox, bytes], None] | None = None

    def send(self, data: bytes) -> None:
        """Send up to 255 bytes, as one message."""
        if len(data) > 255:
            raise ValueError(f"host data is at most 255 bytes per message, not {len(data)}")
        if self._send is None:
            raise TargetError("no firmware is running: send data from on_checkpoint, while it runs")
        self._send(Mbox.HOST_DATA, bytes(data))

    def send_u32(self, value: int) -> None:
        """Send a 32-bit value (``pastel_receive_u32()``)."""
        self.send(struct.pack("<I", value & 0xFFFF_FFFF))


@dataclass
class FirmwareResult:
    """What a firmware run produced.

    Attributes:
        result: ``test_main``'s return value (or ``pastel_finish``'s).
        errors: Errors the firmware reported (``PASTEL_ERROR``, a failed
            ``PASTEL_CHECK`` or ``PASTEL_ASSERT``), in order.
        output: Everything the firmware printed.
        fault: The fault, if the firmware took one and the test expected it
            (``expect_fault``); otherwise a fault is raised.
        checkpoints: Checkpoint ids in the order reported.
        data: Payloads of ``pastel_send`` calls, in order.
    """

    result: int
    errors: list[str] = field(default_factory=list)
    output: str = ""
    checkpoints: list[int] = field(default_factory=list)
    data: list[bytes] = field(default_factory=list)
    fault: FirmwareFault | None = None

    @property
    def passed(self) -> bool:
        """True if ``test_main`` returned 0, with no errors reported and no fault."""
        return self.result == 0 and not self.errors and self.fault is None

    def describe(self, output_lines: int = 20) -> str:
        """Why the firmware failed (or that it passed), with the end of its output."""
        out = [str(self.fault)] if self.fault else []
        if self.result and not self.fault:
            out.append(f"test_main returned {self.result}")
        if self.errors:
            out.append(f"{len(self.errors)} error{'s' if len(self.errors) != 1 else ''}:")
            out += [f"  {e}" for e in self.errors]
        lines = self.output.rstrip("\n").splitlines()
        if lines:
            shown = lines[-output_lines:]
            cut = f" (last {len(shown)} lines)" if len(lines) > len(shown) else ""
            out.append(f"output{cut}:")
            out += [f"  {line}" for line in shown]
        return "\n".join(out) or "passed"


class FirmwareFailed(TargetError):
    """The firmware finished, but failed: a non-zero result, or errors it reported.

    Attributes:
        result: The :class:`FirmwareResult`.
    """

    def __init__(self, result: FirmwareResult, why: str = "firmware failed") -> None:
        super().__init__(f"{why}: {result.describe()}")
        self.result = result


class FirmwareFault(TargetError):
    """The firmware took an unexpected exception."""

    def __init__(self, cause: int, pc: int, tval: int, output: str) -> None:
        super().__init__(f"firmware fault: mcause={cause:#x} mepc={pc:#010x} mtval={tval:#x}")
        self.cause, self.pc, self.tval, self.output = cause, pc, tval, output


def run_firmware(
    harness: PlatformHarness,
    elf: Path,
    *,
    chip_ms: float | None = None,
    timeout_s: float | None = None,
    on_checkpoint: Callable[[int], None] | None = None,
    poll_s: float = 0.005,
    expect_fault: bool = False,
    expect_errors: bool = False,
    link: HostLink | None = None,
) -> FirmwareResult:
    """Reset the target, load and run ``elf``, and wait for it to finish.

    Checkpoints sent with ``wait`` set are acknowledged after ``on_checkpoint``
    returns, so host code can act at a known point in the firmware. Firmware
    calls to ``pastel_waves`` are passed to :meth:`Platform.set_waves
    <soc_pastel.core.platform.Platform.set_waves>`.

    With ``expect_fault``, a fault ends the run and is returned in
    :attr:`FirmwareResult.fault` instead of raised. With ``expect_errors``,
    errors the firmware reports are logged at :data:`XERROR` rather than
    ERROR. Either way the result says what happened.

    ``link``, if given, is connected while the firmware runs, so
    ``on_checkpoint`` can send it data.

    Time limits: the test's own (:attr:`harness.timeout
    <soc_pastel.core.harness.PlatformHarness.timeout>`) apply throughout.
    ``chip_ms`` starts a new budget of that much chip time when the firmware
    starts. ``timeout_s`` adds a plain wall-clock limit.

    Raises:
        FirmwareFault: The firmware took an exception, and ``expect_fault`` wasn't set.
        PastelTimeout: The test timed out, or the firmware did not finish within
            ``timeout_s``.
    """
    ocd = harness.ocd
    ocd.reset(halt=True)
    entry = load_elf(harness, elf)
    cb = elf_symbol(elf, "_SEGGER_RTT")
    rtt = Rtt(memory_for(harness, cb, 64), cb)
    ocd.resume(entry)
    if chip_ms is not None:
        harness.timeout.restart(chip_ms=chip_ms)
    log.info("running %s from %#x", elf.name, entry)

    deadline = None if timeout_s is None else time.monotonic() + timeout_s

    def check(what: str) -> None:
        """Raise if the test has timed out, or ``timeout_s`` has passed."""
        harness.timeout.check()
        if deadline is not None and time.monotonic() > deadline:
            raise PastelTimeout(f"{what} within {timeout_s:g} s")

    rtt.wait_ready(check=lambda: check("the firmware did not start RTT"))

    def send(mid: Mbox, payload: bytes) -> None:
        msg = bytes([mid, len(payload)]) + payload
        while msg:
            msg = msg[rtt.write_down(MAILBOX_CHANNEL, msg) :]
            if msg:  # the buffer is full: wait for the firmware to read it
                check("the firmware did not take the host's data")
                time.sleep(poll_s)

    if link is not None:
        link._send = send
    try:
        return _poll(harness, rtt, send, check, on_checkpoint, poll_s,
                     expect_fault, expect_errors)  # fmt: skip
    finally:
        if link is not None:
            link._send = None


def _poll(
    harness: PlatformHarness,
    rtt: Rtt,
    send: Callable[[Mbox, bytes], None],
    check: Callable[[str], None],
    on_checkpoint: Callable[[int], None] | None,
    poll_s: float,
    expect_fault: bool,
    expect_errors: bool,
) -> FirmwareResult:
    """Read the firmware's output and mailbox until it finishes."""
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
                cp, wait = struct.unpack("<IB", payload)
                result.checkpoints.append(cp)
                log.info("checkpoint %d%s", cp, " (waiting)" if wait else "")
                if on_checkpoint:
                    on_checkpoint(cp)
                if wait:  # an ack for one that didn't wait would be left over
                    send(Mbox.HOST_ACK, payload[:4])
            elif mid == Mbox.ERROR:
                message = payload.decode(errors="replace")
                result.errors.append(message)
                level = XERROR if expect_errors else logging.ERROR
                log.log(level, "firmware error: %s", message)
            elif mid == Mbox.WAVES:
                on = harness.platform.set_waves(payload[:1] != b"\x00")
                send(Mbox.HOST_WAVES, bytes([on]))
            elif mid == Mbox.DATA:
                result.data.append(payload)
            elif mid == Mbox.FINISH:
                (result.result,) = struct.unpack("<i", payload)
                log.info("firmware finished: test_main returned %d", result.result)
                return result
            elif mid == Mbox.FAULT:
                fault = FirmwareFault(*struct.unpack("<III", payload), result.output)
                if expect_fault:
                    log.log(XERROR, "%s (expected)", fault)
                    result.fault = fault
                    return result
                log.error("%s", fault)
                raise fault
        check("the firmware did not finish")
        if not harness.platform.running:
            raise TargetError("platform stopped while firmware was running")
        time.sleep(poll_s)
