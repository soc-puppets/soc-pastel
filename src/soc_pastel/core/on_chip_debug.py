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
from enum import Enum
from typing import ClassVar

from .interface import Interface
from .memory import MemoryInterface


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

    @abstractmethod
    def resume(self, pc: int | None = None) -> None:
        """Resume execution, optionally from ``pc``.

        Implementations perform any cache maintenance the CPU needs before
        running freshly loaded code (e.g. VeeR's fence via its ``dmst`` CSR).
        """

    @abstractmethod
    def step(self) -> None:
        """Execute one instruction and halt."""

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

    @abstractmethod
    def add_breakpoint(self, addr: int, *, hardware: bool = True) -> int:
        """Set a breakpoint at ``addr``; return an id for :meth:`remove_breakpoint`."""

    @abstractmethod
    def remove_breakpoint(self, breakpoint_id: int) -> None:
        """Remove a breakpoint set by :meth:`add_breakpoint`."""

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
