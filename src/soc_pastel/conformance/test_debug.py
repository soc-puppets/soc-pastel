"""On-chip debug: the path the framework itself depends on."""

import time

import pytest

from soc_pastel.core import TargetState


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
    """fw/breakpoints.c, started and waiting at checkpoint 1 for breakpoints to be set."""

    def __init__(self, firmware):
        self.firmware = firmware
        self.harness = firmware.harness
        self.ocd = firmware.harness.ocd
        self.run = None

    def start(self, *symbols, hardware):
        """Start the firmware, set breakpoints on ``symbols`` and let it loop; return their ids."""
        self.run = self.firmware.start("fw/breakpoints.c")
        with self.run.at_checkpoint(1), self.ocd.halted():  # breakpoints need a halted CPU
            return [self.ocd.add_breakpoint(self.addr(s), hardware=hardware) for s in symbols]

    def addr(self, symbol):
        """The address of ``symbol`` in the firmware."""
        return self.firmware.symbol(symbol)

    def count(self, which):
        """How often ``{which}_target`` has been called (the CPU must be halted)."""
        return self.harness.memory().read32(self.addr(f"{which}_count"))


@pytest.fixture
def looping(firmware):
    return Looping(firmware)


HW_SW = pytest.mark.parametrize("hardware", [True, False], ids=["hw", "sw"])


@HW_SW
def test_breakpoint_halts_and_resume_runs_on(looping, hardware):
    ocd = looping.ocd
    bps = looping.start("first_target", hardware=hardware)
    first, second = looping.addr("first_target"), looping.addr("second_target")
    assert ocd.wait_halted() == first and looping.count("first") == 0
    bps.append(ocd.add_breakpoint(second, hardware=hardware))
    ocd.resume()  # runs on from the breakpoint it is halted at
    assert ocd.wait_halted() == second and looping.count("first") == 1
    ocd.resume()
    assert ocd.wait_halted() == first and looping.count("second") == 1
    for bp in bps:
        ocd.remove_breakpoint(bp)
    ocd.resume()
    time.sleep(0.5)
    assert ocd.state() is TargetState.RUNNING  # no breakpoints left
    assert "looping" in looping.run.stop().output  # its output was read meanwhile


@HW_SW
def test_step_from_a_breakpoint(looping, hardware):
    ocd = looping.ocd
    (bp,) = looping.start("first_target", hardware=hardware)
    first = looping.addr("first_target")
    assert ocd.wait_halted() == first
    ocd.step()
    assert ocd.state() is TargetState.HALTED and ocd.pc != first
    ocd.resume()  # round the loop, to the same breakpoint
    assert ocd.wait_halted() == first and looping.count("first") == 1
    ocd.remove_breakpoint(bp)
