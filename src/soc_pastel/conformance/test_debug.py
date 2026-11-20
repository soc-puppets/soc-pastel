"""On-chip debug: the path the framework itself depends on."""

import time

import pytest

from soc_pastel.core import TargetState
from soc_pastel.core.loader import elf_symbol, load_elf, memory_for


def test_reset_halts_at_reset_vector(harness):
    harness.ocd.reset(halt=True)
    assert harness.ocd.state() is TargetState.HALTED
    assert harness.ocd.pc == harness.device.cpu.reset_vector


def test_register_write_read(harness):
    harness.ocd.reset(halt=True)
    harness.ocd.write_register("a0", 0x1234_5678)
    assert harness.ocd.read_register("a0") == 0x1234_5678


def test_halt_after_resume(harness):
    harness.ocd.reset(halt=True)
    harness.ocd.resume()
    harness.ocd.halt()
    assert harness.ocd.state() is TargetState.HALTED


# -- Breakpoints and stepping ---------------------------------------------------------------


class Looping:
    """fw/breakpoints.c, loaded and halted at its entry point."""

    def __init__(self, firmware):
        self.harness = firmware.harness
        self.ocd = firmware.harness.ocd
        self.elf = firmware.build("fw/breakpoints.c")
        self.ocd.reset(halt=True)
        self.entry = load_elf(self.harness, self.elf)

    def addr(self, symbol):
        """The address of ``symbol`` in the firmware."""
        return elf_symbol(self.elf, symbol)

    def count(self, which):
        """How often ``{which}_target`` has been called (the CPU must be halted)."""
        addr = self.addr(f"{which}_count")
        return memory_for(self.harness, addr, 4).read32(addr)

    def wait_halted(self, timeout_s=120.0):
        """Wait for the CPU to halt (at a breakpoint); return the PC."""
        deadline = time.monotonic() + timeout_s
        while self.ocd.state() is not TargetState.HALTED:
            self.harness.timeout.check()
            if time.monotonic() > deadline:
                self.ocd.halt()
                pytest.fail(f"no breakpoint hit within {timeout_s:.0f} s (pc {self.ocd.pc:#x})")
            time.sleep(0.05)
        return self.ocd.pc


@pytest.fixture
def looping(firmware):
    return Looping(firmware)


HW_SW = pytest.mark.parametrize("hardware", [True, False], ids=["hw", "sw"])


@HW_SW
def test_breakpoint_halts_and_resume_runs_on(looping, hardware):
    ocd = looping.ocd
    first, second = looping.addr("first_target"), looping.addr("second_target")
    bps = [ocd.add_breakpoint(first, hardware=hardware)]
    ocd.resume(looping.entry)
    assert looping.wait_halted() == first and looping.count("first") == 0
    bps.append(ocd.add_breakpoint(second, hardware=hardware))
    ocd.resume()  # runs on from the breakpoint it is halted at
    assert looping.wait_halted() == second and looping.count("first") == 1
    ocd.resume()
    assert looping.wait_halted() == first and looping.count("second") == 1
    for bp in bps:
        ocd.remove_breakpoint(bp)
    ocd.resume()
    time.sleep(0.5)
    assert ocd.state() is TargetState.RUNNING  # no breakpoints left


@HW_SW
def test_step_from_a_breakpoint(looping, hardware):
    ocd = looping.ocd
    first = looping.addr("first_target")
    bp = ocd.add_breakpoint(first, hardware=hardware)
    ocd.resume(looping.entry)
    assert looping.wait_halted() == first
    ocd.step()
    assert ocd.state() is TargetState.HALTED and ocd.pc != first
    ocd.resume()  # round the loop, to the same breakpoint
    assert looping.wait_halted() == first and looping.count("first") == 1
    ocd.remove_breakpoint(bp)
