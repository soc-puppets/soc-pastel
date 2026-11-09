"""On-chip debug: the path the framework itself depends on."""

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
