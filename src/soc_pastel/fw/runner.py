"""Running test firmware and collecting its result."""

from __future__ import annotations

import logging
import struct
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
        finished: True once ``test_main`` has returned (or ``pastel_finish``
            was called); False for a run stopped before then.
    """

    result: int
    errors: list[str] = field(default_factory=list)
    output: str = ""
    checkpoints: list[int] = field(default_factory=list)
    data: list[bytes] = field(default_factory=list)
    fault: FirmwareFault | None = None
    finished: bool = False

    @property
    def passed(self) -> bool:
        """True if ``test_main`` returned 0, with no errors reported and no fault."""
        return self.result == 0 and not self.errors and self.fault is None

    def describe(self, output_lines: int = 20) -> str:
        """Why the firmware failed (or that it passed), with the end of its output."""
        out = [str(self.fault)] if self.fault else []
        if self.finished and self.result and not self.fault:
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


def check_result(result: FirmwareResult, *, expect_pass: bool, expect_fault: bool) -> None:
    """Raise :class:`FirmwareFailed` unless ``result`` is what the test expected.

    A finished run must pass (unless ``expect_pass`` is False), or must have
    faulted if ``expect_fault``. A run stopped before finishing only fails
    for errors it reported.
    """
    if expect_fault:
        if result.fault is None:
            raise FirmwareFailed(result, "expected a fault, but the firmware finished")
    elif not expect_pass:
        return
    elif result.finished and not result.passed:
        raise FirmwareFailed(result)
    elif result.errors:
        raise FirmwareFailed(result, "firmware reported errors")


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
    run = FirmwareRun(
        harness, elf, chip_ms=chip_ms, timeout_s=timeout_s, on_checkpoint=on_checkpoint,
        poll_s=poll_s, expect_fault=expect_fault, expect_errors=expect_errors, link=link,
    )  # fmt: skip
    return run._run_here()


class FirmwareRun:
    """Firmware running on the target while the test carries on.

    ``firmware.start()`` makes one: the firmware is built, loaded and started,
    and a background thread then does what :func:`run_firmware` does while it
    waits: it prints the firmware's output and collects its data, errors,
    checkpoints, fault and result, and watches the test's time limit. The
    test is free to drive pins, set breakpoints or anything else meanwhile,
    and uses these methods to keep in step with the firmware.

    A *waiting* checkpoint (``pastel_checkpoint(id, 1)``) holds the firmware
    until the test lets it go: with :meth:`at_checkpoint`, :meth:`release`, or
    :meth:`wait`, which lets every checkpoint go as it runs to the end.

    :attr:`result` is what the firmware has produced so far.
    """

    def __init__(
        self,
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
        check: bool = False,
    ) -> None:
        self.harness = harness
        self._check_result = check
        self.elf = elf
        self._chip_ms = chip_ms
        self._timeout_s = timeout_s
        self._on_checkpoint = on_checkpoint
        self._poll_s = poll_s
        self._expect_fault = expect_fault
        self._expect_errors = expect_errors
        self._link = link
        #: What the firmware has produced so far.
        self.result = FirmwareResult(result=-1)
        self._text: list[str] = []
        self._line = bytearray()
        self._mbox = bytearray()
        self._rtt: Rtt | None = None
        self._deadline: float | None = None
        self._send_lock = threading.Lock()  # RTT down-channel writes, from either thread
        self._cv = threading.Condition()
        self._finished = False  # test_main returned, or an expected fault
        self._error: BaseException | None = None  # what ended the background thread
        self._seen: list[int] = []  # checkpoint ids, in order
        self._taken: Counter[int] = Counter()  # how many of each wait_checkpoint() returned
        self._held: list[bytes] = []  # waiting checkpoints not yet acknowledged
        self._release_all = False  # wait(): acknowledge everything from now on
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    # -- Starting -----------------------------------------------------------------------

    def _launch(self) -> None:
        h = self.harness
        ocd = h.ocd
        ocd.reset(halt=True)
        entry = load_elf(h, self.elf)
        cb = elf_symbol(self.elf, "_SEGGER_RTT")
        self._rtt = Rtt(memory_for(h, cb, 64), cb)
        ocd.resume(entry)
        if self._chip_ms is not None:
            h.timeout.restart(chip_ms=self._chip_ms)
        log.info("running %s from %#x", self.elf.name, entry)
        if self._timeout_s is not None:
            self._deadline = time.monotonic() + self._timeout_s
        self._rtt.wait_ready(check=lambda: self._check("the firmware did not start RTT"))
        if self._link is not None:
            self._link._send = self._send

    def _check(self, what: str) -> None:
        """Raise if the test has timed out, or ``timeout_s`` has passed."""
        self.harness.timeout.check()
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise PastelTimeout(f"{what} within {self._timeout_s:g} s")

    def _send(self, mid: Mbox, payload: bytes) -> None:
        msg = bytes([mid, len(payload)]) + payload
        with self._send_lock:
            assert self._rtt is not None
            while msg:
                msg = msg[self._rtt.write_down(MAILBOX_CHANNEL, msg) :]
                if msg:  # the buffer is full: wait for the firmware to read it
                    self._check("the firmware did not take the host's data")
                    time.sleep(self._poll_s)

    def _run_here(self) -> FirmwareResult:
        """Run to the end in this thread, calling ``on_checkpoint`` (run_firmware)."""
        self._release_all = True
        self._launch()
        try:
            while not self._poll():
                self._check("the firmware did not finish")
                if not self.harness.platform.running:
                    raise TargetError("platform stopped while firmware was running")
                time.sleep(self._poll_s)
        finally:
            self._detach()
        return self.result

    def _start_background(self) -> None:
        """Load and start the firmware here, then follow it from a background thread."""
        self._launch()
        self._thread = threading.Thread(target=self._follow, name="pastel firmware", daemon=True)
        self._thread.start()

    def _follow(self) -> None:
        try:
            while not self._stop.is_set():
                if self._poll():
                    return
                self._check("the firmware did not finish")
                if not self.harness.platform.running:
                    raise TargetError("platform stopped while firmware was running")
                time.sleep(self._poll_s)
        except BaseException as e:
            with self._cv:
                self._error = e
                self._cv.notify_all()

    # -- One look at the firmware's output ----------------------------------------------

    def _poll(self) -> bool:
        """Read the firmware's output and mailbox once; return True if it has finished."""
        assert self._rtt is not None
        chunk = self._rtt.read_up(TEXT_CHANNEL)
        if chunk:
            self._text.append(chunk.decode(errors="replace"))
            self._line += chunk
            *done, rest = self._line.split(b"\n")
            for ln in done:
                log.info("fw: %s", ln.decode(errors="replace"))
            self._line = bytearray(rest)
            self.result.output = "".join(self._text)
        self._mbox += self._rtt.read_up(MAILBOX_CHANNEL)
        while len(self._mbox) >= 2 and len(self._mbox) >= 2 + self._mbox[1]:
            mid, n = self._mbox[0], self._mbox[1]
            payload = bytes(self._mbox[2 : 2 + n])
            del self._mbox[: 2 + n]
            if self._handle(mid, payload):
                with self._cv:
                    self._finished = True
                    self._cv.notify_all()
                return True
        return False

    def _handle(self, mid: int, payload: bytes) -> bool:
        """Act on one mailbox message; return True if the firmware has finished."""
        result = self.result
        if mid == Mbox.HELLO:
            if payload[:1] != bytes([MAILBOX_VERSION]):
                raise ProtocolError(f"firmware mailbox version {payload[:1]!r} unsupported")
        elif mid == Mbox.CHECKPOINT:
            cp, wait = struct.unpack("<IB", payload)
            result.checkpoints.append(cp)
            log.info("checkpoint %d%s", cp, " (waiting)" if wait else "")
            if self._on_checkpoint:
                self._on_checkpoint(cp)
            with self._cv:
                self._seen.append(cp)
                hold = wait and not self._release_all
                if hold:
                    self._held.append(payload[:4])
                self._cv.notify_all()
            if wait and not hold:  # an ack for one that didn't wait would be left over
                self._send(Mbox.HOST_ACK, payload[:4])
        elif mid == Mbox.ERROR:
            message = payload.decode(errors="replace")
            result.errors.append(message)
            level = XERROR if self._expect_errors else logging.ERROR
            log.log(level, "firmware error: %s", message)
        elif mid == Mbox.WAVES:
            on = self.harness.platform.set_waves(payload[:1] != b"\x00")
            self._send(Mbox.HOST_WAVES, bytes([on]))
        elif mid == Mbox.DATA:
            result.data.append(payload)
        elif mid == Mbox.FINISH:
            (result.result,) = struct.unpack("<i", payload)
            result.finished = True
            log.info("firmware finished: test_main returned %d", result.result)
            return True
        elif mid == Mbox.FAULT:
            fault = FirmwareFault(*struct.unpack("<III", payload), result.output)
            if self._expect_fault:
                log.log(XERROR, "%s (expected)", fault)
                result.fault = fault
                return True
            log.error("%s", fault)
            raise fault
        return False

    # -- For tests, while the firmware runs ---------------------------------------------

    @property
    def finished(self) -> bool:
        """True once the firmware has finished (or taken an expected fault)."""
        return self._finished

    def send(self, data: bytes) -> None:
        """Send up to 255 bytes to the firmware (``pastel_receive()``)."""
        if len(data) > 255:
            raise ValueError(f"host data is at most 255 bytes per message, not {len(data)}")
        self._send(Mbox.HOST_DATA, bytes(data))

    def send_u32(self, value: int) -> None:
        """Send a 32-bit value to the firmware (``pastel_receive_u32()``)."""
        self.send(struct.pack("<I", value & 0xFFFF_FFFF))

    def _wait_until(self, ready: Callable[[], bool], what: str) -> None:
        """Wait, under the condition, until ``ready()``; raise what stopped the firmware."""
        began, warned = time.monotonic(), False
        while True:
            with self._cv:
                if ready():
                    return
                if self._error is not None:
                    raise self._error
                if self._finished:
                    raise TargetError(f"the firmware finished before {what}")
                held = [struct.unpack("<I", ack)[0] for ack in self._held]
                self._cv.wait(0.05)
            if held and not warned and time.monotonic() - began > 2:
                warned = True  # the commonest way to wait for ever: say so
                log.warning(
                    "waiting for %s, but the firmware is held at waiting checkpoint %s "
                    "until the test releases it (at_checkpoint() or release())",
                    what, ", ".join(map(str, held)),
                )  # fmt: skip
            self._check(f"the firmware did not reach {what}")

    def wait_checkpoint(self, checkpoint: int) -> None:
        """Wait until the firmware reports ``checkpoint``.

        Each call takes one report, so a repeated id is waited for again; a
        report that came before the call counts. A waiting checkpoint stays
        held until :meth:`release`.
        """
        self._wait_until(
            lambda: self._seen.count(checkpoint) > self._taken[checkpoint],
            f"checkpoint {checkpoint}",
        )
        self._taken[checkpoint] += 1

    def release(self, checkpoint: int) -> None:
        """Let the firmware go on from a waiting ``checkpoint`` (nothing if it isn't held)."""
        ack = struct.pack("<I", checkpoint)
        with self._cv:
            if ack not in self._held:
                return
            self._held.remove(ack)
        self._send(Mbox.HOST_ACK, ack)

    @contextmanager
    def at_checkpoint(self, checkpoint: int) -> Iterator[None]:
        """Wait for ``checkpoint``; if it waits, the firmware is held until the block ends."""
        self.wait_checkpoint(checkpoint)
        try:
            yield
        finally:
            self.release(checkpoint)

    def wait(self) -> FirmwareResult:
        """Let the firmware run to the end, releasing every checkpoint, and return its result.

        Fails as ``firmware.run()`` would: on an unexpected fault, a non-zero
        result or reported errors, unless the run was started expecting them.
        """
        with self._cv:
            self._release_all = True
            held, self._held = self._held, []
        for ack in held:
            self._send(Mbox.HOST_ACK, ack)
        try:
            self._wait_until(lambda: self._finished, "finishing")
        finally:
            self._close()
        self._judge()
        return self.result

    def _judge(self) -> None:
        if self._check_result:
            check_result(
                self.result, expect_pass=not self._expect_errors, expect_fault=self._expect_fault
            )

    def stop(self) -> FirmwareResult:
        """Stop following firmware that won't finish by itself; return what it produced.

        The firmware keeps running on the target. Raises whatever went wrong
        while it ran: an unexpected fault, a timeout, a lost connection.
        """
        self._close()
        with self._cv:
            error = self._error
        if error is not None:
            raise error
        self._judge()
        return self.result

    def _close(self) -> None:
        """Stop the background thread and disconnect the host link. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=30)
        self._detach()

    def _detach(self) -> None:
        if self._link is not None:
            self._link._send = None
