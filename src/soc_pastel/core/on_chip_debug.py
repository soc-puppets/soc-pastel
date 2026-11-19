"""Contract for the on-chip debug interface.

On-chip debug is the one interface the framework itself depends on: the
loader, firmware tests, frontdoor memory access and
the RTT reader all go through it, so every device must bind one. Otherwise it
is an interface like any other, with implementations per platform (OpenOCD
over remote_bitbang in simulation, OpenOCD over FTDI, J-Link, pyOCD).

The harness exposes it as ``harness.ocd``.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from .interface import Interface
from .memory import MemoryInterface


@dataclass
class _Breakpoint:
    addr: int
    hardware: bool
    #: The driver's id for it, which may change when it is set again.
    driver_id: int


class TargetState(Enum):
    """Execution state of the target CPU."""

    UNKNOWN = "unknown"
    RUNNING = "running"
    HALTED = "halted"
    RESET = "reset"


class OnChipDebug(Interface):
    """Control the target CPU through its debug port.

    All methods raise :class:`~soc_pastel.core.errors.TargetError` if the
    target does not respond as required.

    **Breakpoints.** When execution starts (:meth:`resume`, :meth:`step`) at
    an address with a breakpoint set by :meth:`add_breakpoint`, that
    breakpoint is stepped over: the CPU runs on from a breakpoint it halted
    at, rather than halting there again. RISC-V debug leaves this to the
    debugger (a trigger fires again before the instruction runs), so this
    class does it for every driver: it removes the breakpoint, steps one
    instruction and sets it again.

    **Writing a driver.** Implement the abstract methods. Run control and
    breakpoints are :meth:`_resume`, :meth:`_step`, :meth:`_add_breakpoint`
    and :meth:`_remove_breakpoint`: plain operations, with no stepping over.
    """

    type_name: ClassVar[str] = "on_chip_debug"

    @abstractmethod
    def reset(self, *, halt: bool = True) -> None:
        """Reset the target.

        With ``halt=True`` the CPU must be halted at its reset vector before
        executing any instruction, so firmware can be loaded without racing a
        boot ROM. Implementations must verify the halt, not assume it.
        """

    @abstractmethod
    def halt(self) -> None:
        """Halt the CPU and wait until it reports halted."""

    def resume(self, pc: int | None = None) -> None:
        """Resume execution, optionally from ``pc``, stepping over a breakpoint there."""
        if self._breakpoints:
            if pc is not None and self._breakpoint_ids_at(pc):
                self.write_register("pc", pc)
                pc = None
            self._step_over_breakpoint()
        self._resume(pc)

    def step(self) -> None:
        """Execute one instruction and halt, even if there is a breakpoint on it."""
        if not self._step_over_breakpoint():
            self._step()

    @abstractmethod
    def state(self) -> TargetState:
        """Return the CPU's current execution state, as reported by the target."""

    @abstractmethod
    def read_register(self, name: str) -> int:
        """Read a core register or CSR by name (``"pc"``, ``"a0"``, ``"mcause"``)."""

    @abstractmethod
    def write_register(self, name: str, value: int) -> None:
        """Write a core register or CSR by name. The CPU must be halted."""

    @property
    @abstractmethod
    def memory(self) -> MemoryInterface:
        """Memory access through the debug port (the frontdoor)."""

    def add_breakpoint(self, addr: int, *, hardware: bool = True) -> int:
        """Set a breakpoint at ``addr``; return an id for :meth:`remove_breakpoint`."""
        breakpoint_id = self._add_breakpoint(addr, hardware=hardware)
        self._breakpoints[breakpoint_id] = _Breakpoint(addr, hardware, breakpoint_id)
        return breakpoint_id

    def remove_breakpoint(self, breakpoint_id: int) -> None:
        """Remove a breakpoint set by :meth:`add_breakpoint`."""
        bp = self._breakpoints.get(breakpoint_id)
        self._remove_breakpoint(bp.driver_id if bp else breakpoint_id)
        self._breakpoints.pop(breakpoint_id, None)

    # -- For drivers to implement ----------------------------------------------------

    @abstractmethod
    def _resume(self, pc: int | None) -> None:
        """Resume execution, optionally from ``pc``.

        Implementations perform any cache maintenance the CPU needs before
        running freshly loaded code (e.g. VeeR's fence via its ``dmst`` CSR).
        """

    @abstractmethod
    def _step(self) -> None:
        """Execute one instruction and halt."""

    @abstractmethod
    def _add_breakpoint(self, addr: int, *, hardware: bool) -> int:
        """Set a breakpoint at ``addr``; return the driver's id for it."""

    @abstractmethod
    def _remove_breakpoint(self, breakpoint_id: int) -> None:
        """Remove the breakpoint with the driver's id ``breakpoint_id``."""

    # -- Stepping over breakpoints ---------------------------------------------------

    @property
    def _breakpoints(self) -> dict[int, _Breakpoint]:
        """Breakpoints set through :meth:`add_breakpoint`, by the id it returned."""
        return self.__dict__.setdefault("_pastel_breakpoints", {})

    def _breakpoint_ids_at(self, addr: int) -> list[int]:
        return [i for i, bp in self._breakpoints.items() if bp.addr == addr]

    def _step_over_breakpoint(self) -> bool:
        """If halted on breakpoints of ours, step over them; return True if it stepped."""
        if not self._breakpoints or self.state() is not TargetState.HALTED:
            return False
        ids = self._breakpoint_ids_at(self.pc)
        if not ids:
            return False
        for i in ids:
            self._remove_breakpoint(self._breakpoints[i].driver_id)
        try:
            self._step()
        finally:
            for i in ids:
                bp = self._breakpoints[i]
                bp.driver_id = self._add_breakpoint(bp.addr, hardware=bp.hardware)
        return True

    @property
    def pc(self) -> int:
        """The program counter. The CPU must be halted."""
        return self.read_register("pc")

    @contextmanager
    def halted(self) -> Iterator[None]:
        """Halt the CPU for the duration of the block, then resume it if it was running.

        Use it around memory accesses the debug path only allows while halted
        (e.g. VeeR's DCCM).
        """
        was_running = self.state() is TargetState.RUNNING
        if was_running:
            self.halt()
        try:
            yield
        finally:
            if was_running:
                self.resume()
