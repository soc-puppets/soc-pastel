"""Test timeouts, in chip time.

A test's time limit is in **chip time**: how long its work would take on the
chip itself, at its own clock (:attr:`Device.clock_mhz
<soc_pastel.core.device.Device.clock_mhz>`). Each platform measures it its own
way, scaled by how fast the build runs compared with the chip
(:meth:`Device.chip_speed <soc_pastel.core.device.Device.chip_speed>`):

- a simulator by simulation time;
- hardware by wall-clock time, so an FPGA build at 20 MHz of a 1 GHz chip gets
  50 times as long, and silicon exactly as long.

A test times out once its chip time has passed the limit **and** a further
grace period of wall-clock time has gone by. The grace covers what doesn't
scale with the chip's clock: debug transfers, loading firmware over JTAG, the
host's own work. It is negligible next to a simulation, and generous on
hardware. Separately, a simulation whose time stops advancing for ``stall_s``
of wall-clock time is stuck, and times out too.

The clock starts when the harness has opened (the platform is up and
on-chip debug connected), so building and queueing don't count.
``firmware.run(chip_ms=...)`` restarts it with a budget of its own when the
firmware starts.

When a test times out, the watchdog stops the platform, so whatever the test
is waiting on fails, and the test is reported as timed out.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .errors import PastelTimeout

if TYPE_CHECKING:
    from .harness import PlatformHarness

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimeoutSettings:
    """A test's time limits.

    Attributes:
        chip_ms: The limit, in milliseconds of chip time; None for no limit.
        grace_s: Wall-clock seconds allowed after ``chip_ms`` has passed.
        stall_s: Wall-clock seconds simulation time may stand still before
            the simulation counts as stuck; None to never check.
    """

    chip_ms: float | None = None
    grace_s: float = 10.0
    stall_s: float | None = None

    @property
    def active(self) -> bool:
        """True if there is anything to watch."""
        return self.chip_ms is not None or self.stall_s is not None


class Watchdog:
    """Watches one test's chip time, and ends the test when it runs out.

    The harness owns one (:attr:`PlatformHarness.timeout
    <soc_pastel.core.harness.PlatformHarness.timeout>`), starts it once it has
    opened and stops it when it closes.
    """

    #: How often to look at the time, in seconds.
    POLL_S = 0.25

    def __init__(self, harness: PlatformHarness) -> None:
        self._harness = harness
        self.settings = TimeoutSettings()
        #: Why the test timed out, once it has.
        self.expired: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_raw = 0  # platform time when the budget started
        self._over_since: float | None = None  # wall time the budget ran out
        self._last_raw: int | None = None  # simulation time last seen ...
        self._last_change = 0.0  # ... and when it last moved

    @property
    def speed(self) -> float:
        """How fast the platform's build runs compared with the chip."""
        h = self._harness
        return h.device.chip_speed(h.variant)

    def configure(self, settings: TimeoutSettings) -> None:
        """Set the limits (before the harness opens)."""
        self.settings = settings

    def _raw(self) -> tuple[int, bool]:
        """Platform time in ns, and whether it is simulation time."""
        sim = self._harness._platform.time_ns()
        return (sim, True) if sim is not None else (time.monotonic_ns(), False)

    def start(self) -> None:
        """Start the clock, if there are limits to watch. The harness calls this."""
        if not self.settings.active or self._thread is not None:
            return
        raw, _ = self._raw()
        with self._lock:
            self._start_raw = raw
            self._over_since = None
            self._last_raw, self._last_change = raw, time.monotonic()
        self._thread = threading.Thread(target=self._watch, name="pastel timeout", daemon=True)
        self._thread.start()

    def restart(self, *, chip_ms: float | None = None, grace_s: float | None = None) -> None:
        """Start a new budget from now: ``chip_ms`` of chip time, then ``grace_s``.

        Arguments left out keep their settings.
        """
        changes = {k: v for k, v in (("chip_ms", chip_ms), ("grace_s", grace_s)) if v is not None}
        self.settings = replace(self.settings, **changes)
        if self._thread is None:
            self.start()
            return
        raw, _ = self._raw()
        with self._lock:
            self._start_raw = raw
            self._over_since = None

    def stop(self) -> None:
        """Stop watching. Safe to call more than once."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            # Long enough for an expiry already under way to finish stopping
            # the platform, so the harness doesn't stop it at the same time.
            thread.join(timeout=60)

    def check(self) -> None:
        """Raise :class:`PastelTimeout` if the test has timed out."""
        if self.expired:
            raise PastelTimeout(self.expired)

    def _watch(self) -> None:
        while not self._stop.wait(self.POLL_S):
            try:
                raw, is_sim = self._raw()
            except Exception:  # the platform has gone: nothing left to time
                return
            now = time.monotonic()
            with self._lock:
                reason = self._judge(raw, is_sim, now)
            if reason:
                self._expire(reason)
                return

    def _judge(self, raw: int, is_sim: bool, now: float) -> str | None:
        s = self.settings
        if s.chip_ms is not None:
            chip_ms = (raw - self._start_raw) * self.speed / 1e6
            if chip_ms < s.chip_ms:
                self._over_since = None
            elif self._over_since is None:
                self._over_since = now
            elif now - self._over_since >= s.grace_s:
                return self._describe_budget()
        if is_sim and s.stall_s is not None:
            if raw != self._last_raw:
                self._last_raw, self._last_change = raw, now
            elif now - self._last_change >= s.stall_s:
                return (
                    f"timed out: simulation time stopped at {raw / 1e6:.3f} ms "
                    f"for {s.stall_s:g} s (the simulator is stuck)"
                )
        return None

    def _describe_budget(self) -> str:
        s = self.settings
        what = f"{s.chip_ms:g} ms of chip time"
        speed = self.speed
        if speed != 1.0:
            v = self._harness.variant
            what += f" ({s.chip_ms / speed:g} ms at variant {v.name!r}'s {v.clock_mhz:g} MHz)"
        return f"timed out: the test took more than {what}, plus {s.grace_s:g} s grace"

    def _expire(self, reason: str) -> None:
        self.expired = reason
        log.error("%s; stopping the platform", reason)
        try:
            self._harness._platform.stop()
        except Exception:
            log.exception("stopping the platform after a timeout failed")
