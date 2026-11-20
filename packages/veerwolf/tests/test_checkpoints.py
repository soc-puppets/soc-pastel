"""Checkpoints, by example: the examples in docs/guides/writing-tests.rst.

Needs a simulation run, e.g.::

    pytest packages/veerwolf/tests/test_checkpoints.py --device=veerwolf --platform=verilator
"""

import pytest

from soc_pastel.interfaces.gpio import Gpio

BUTTON = 1 << 16


@pytest.fixture(autouse=True)
def _simulation_only(request):
    if request.config.getoption("platform") is None:
        pytest.skip("needs a simulation run (--device=veerwolf --platform=...)")


# Example 1: stimulus at a known point.
@pytest.mark.requires("gpio")
def test_press_a_button_while_the_firmware_waits(harness, firmware):
    gpio = harness.iface("gpio", Gpio)

    def on_checkpoint(cp):
        if cp == 1:
            gpio.drive(BUTTON, BUTTON)

    firmware.run("fw/cp_stimulus.c", on_checkpoint=on_checkpoint)


# Example 2: inspect the chip at a known point.
@pytest.mark.requires("gpio")
def test_inspect_the_chip_while_the_firmware_waits(harness, firmware):
    gpio = harness.iface("gpio", Gpio)

    def on_checkpoint(cp):
        with harness.ocd.halted():  # some memories are only reachable halted
            assert harness.memory().read32(firmware.symbol("total")) == 55
        assert gpio.sample() & 0xFFFF == 55

    firmware.run("fw/cp_inspect.c", on_checkpoint=on_checkpoint)


# Example 3: progress markers, checked afterwards.
def test_progress_markers(firmware):
    result = firmware.run("fw/cp_progress.c")
    assert result.checkpoints == [10, 20, 30]


# Example 4: a step-by-step conversation.
def test_handshake(harness, firmware):
    def on_checkpoint(cp):
        with harness.ocd.halted():
            mem = harness.memory()
            if cp > 1:  # the firmware has answered the previous command
                sent = cp - 1 + 10
                assert mem.read32(firmware.symbol("answer")) == sent * sent
            if cp <= 3:
                mem.write32(firmware.symbol("command"), cp + 10)

    result = firmware.run("fw/cp_handshake.c", on_checkpoint=on_checkpoint)
    assert result.checkpoints == [1, 2, 3, 4]


@pytest.mark.requires("gpio")
def test_a_marker_does_not_release_a_later_waiting_checkpoint(harness, firmware):
    # Drive a pin rather than write memory: that doesn't stop the CPU. The
    # pause gives firmware released early time to read the pin first.
    import time

    gpio = harness.iface("gpio", Gpio)
    seen = []

    def on_checkpoint(cp):
        seen.append(cp)
        if len(seen) == 2:  # the waiting one
            time.sleep(0.2)
            gpio.drive(1 << 17, 1 << 17)

    firmware.run("fw/cp_marker_then_wait.c", on_checkpoint=on_checkpoint)
    assert seen == [1, 1]


def test_symbol_needs_a_build(firmware):
    from soc_pastel.core import ConfigurationError

    with pytest.raises(ConfigurationError, match="no firmware built"):
        firmware.symbol("total")
