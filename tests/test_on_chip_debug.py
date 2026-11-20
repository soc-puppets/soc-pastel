"""OnChipDebug's run control: stepping over breakpoints, for every driver."""

from typing import ClassVar

import pytest

from soc_pastel.core import TargetState
from soc_pastel.core.on_chip_debug import OnChipDebug


class Harness:
    platform = None


class Cpu(OnChipDebug):
    """A CPU that runs a loop of 4-byte instructions from 0x100 to 0x13c, as RISC-V does
    at a breakpoint: it halts *before* running the instruction there, every time."""

    impl_name: ClassVar[str] = "cpu"
    START, END = 0x100, 0x140

    def __init__(self):
        super().__init__("ocd", {}, Harness())
        self.pc_ = self.START
        self.halted_ = True
        self.hw: dict[int, int] = {}  # driver id -> address
        self.next_id = 1
        self.log: list[str] = []

    def _next(self, pc):
        return pc + 4 if pc + 4 < self.END else self.START

    def _resume(self, pc):
        self.log.append("resume")
        if pc is not None:
            self.pc_ = pc
        for _ in range(100):  # run until a breakpoint, or for a while
            if self.pc_ in self.hw.values():
                self.halted_ = True
                return
            self.pc_ = self._next(self.pc_)
        self.halted_ = False

    def _step(self):
        self.log.append("step")
        if self.pc_ not in self.hw.values():  # a breakpoint here fires again
            self.pc_ = self._next(self.pc_)

    def _add_breakpoint(self, addr, *, hardware):
        self.log.append(f"bp {addr:#x}")
        i, self.next_id = self.next_id, self.next_id + 1
        self.hw[i] = addr
        return i

    def _remove_breakpoint(self, breakpoint_id):
        self.log.append(f"rbp {self.hw[breakpoint_id]:#x}")
        del self.hw[breakpoint_id]

    def state(self):
        return TargetState.HALTED if self.halted_ else TargetState.RUNNING

    def read_register(self, name):
        assert name == "pc" and self.halted_
        return self.pc_

    def write_register(self, name, value):
        assert name == "pc" and self.halted_
        self.pc_ = value

    def reset(self, *, halt=True):
        self.pc_, self.halted_ = self.START, halt

    def halt(self):
        self.halted_ = True

    memory = None
    open = close = lambda self: None


@pytest.fixture
def cpu():
    return Cpu()


def test_resume_runs_on_from_a_breakpoint(cpu):
    first = cpu.add_breakpoint(0x110)
    cpu.add_breakpoint(0x120)
    cpu.resume()
    assert cpu.pc == 0x110
    cpu.resume()
    assert cpu.pc == 0x120  # not 0x110 again
    cpu.resume()
    assert cpu.pc == 0x110  # round the loop: both breakpoints still set
    cpu.remove_breakpoint(first)
    cpu.resume()
    assert cpu.pc == 0x120
    cpu.resume()
    assert cpu.pc == 0x120  # the only one left


def test_step_from_a_breakpoint(cpu):
    cpu.add_breakpoint(0x110)
    cpu.resume()
    cpu.step()
    assert cpu.pc == 0x114
    cpu.step()
    assert cpu.pc == 0x118  # an ordinary step
    cpu.resume()
    assert cpu.pc == 0x110  # and the breakpoint is still there


def test_resume_from_a_pc_with_a_breakpoint(cpu):
    cpu.add_breakpoint(0x110)
    cpu.add_breakpoint(0x130)
    cpu.resume(0x110)
    assert cpu.pc == 0x130


def test_a_breakpoint_set_again_keeps_its_id(cpu):
    bp = cpu.add_breakpoint(0x110)
    cpu.resume()
    cpu.resume()  # sets it again, with a new driver id
    cpu.remove_breakpoint(bp)
    assert cpu.hw == {}


def test_no_breakpoints_no_extra_work(cpu):
    cpu.resume(0x104)
    cpu.halt()
    cpu.step()
    assert cpu.log == ["resume", "step"]


def test_away_from_breakpoints_resume_is_plain(cpu):
    cpu.add_breakpoint(0x110)
    cpu.log.clear()
    cpu.resume()
    assert cpu.log == ["resume"]
