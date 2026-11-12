"""The pastel_gpio agent on VeeRwolf's GPIO, in simulation.

Needs a simulation run, e.g.::

    pytest packages/veerwolf/tests/test_gpio.py --device=veerwolf --platform=verilator

VeeRwolf's testbench: pins 15:0 are chip outputs, pins 31:16 chip inputs that
the agent drives (low when it doesn't). The syscon register at 0x80001010
sets the outputs when written and returns all 32 pins when read.
"""

import struct

import pytest

from soc_pastel.interfaces.gpio import Gpio

GPIO_REG = 0x8000_1010
OUTPUTS, INPUTS = 0x0000_FFFF, 0xFFFF_0000


@pytest.fixture(autouse=True)
def _simulation_only(request):
    if request.config.getoption("platform") is None:
        pytest.skip("needs a simulation run (--device=veerwolf --platform=...)")


@pytest.fixture
def gpio(harness):
    harness.require("gpio")
    return harness.iface("gpio", Gpio)


@pytest.fixture
def debug_memory(harness):
    return harness.memory(prefer_backdoor=False)


def test_width_comes_from_the_testbench(gpio):
    assert gpio.width == 32


def test_chip_sees_what_the_agent_drives(gpio, debug_memory):
    gpio.drive(0x1234_0000, INPUTS)
    assert gpio.sample() & INPUTS == 0x1234_0000
    assert debug_memory.read32(GPIO_REG) & INPUTS == 0x1234_0000
    gpio.drive(0xFFFF_0000, 0x00F0_0000)  # only the selected pins change
    assert debug_memory.read32(GPIO_REG) & INPUTS == 0x12F4_0000


def test_agent_sees_what_the_chip_drives(gpio, debug_memory):
    debug_memory.write32(GPIO_REG, 0x5A3C)
    assert gpio.sample() & OUTPUTS == 0x5A3C


def test_release_stops_driving(gpio, debug_memory):
    gpio.drive(0xFFFF_0000, INPUTS)
    gpio.release(0x0F00_0000)
    assert gpio.sample() & INPUTS == 0xF0FF_0000
    gpio.release(INPUTS)
    assert debug_memory.read32(GPIO_REG) & INPUTS == 0


def test_no_unknown_pins(gpio):
    assert gpio.sample_unknown() == 0


def test_masks_beyond_the_pins_are_rejected(gpio):
    with pytest.raises(ValueError, match="beyond"):
        gpio.drive(0, 1 << 32)


def test_firmware_and_agent_take_turns(gpio, firmware):
    def on_checkpoint(cp):
        if cp == 1:
            assert gpio.sample() & OUTPUTS == 0xA5C3
            gpio.drive(0xBEEF_0000, INPUTS)
        elif cp == 2:
            gpio.release(INPUTS)

    result = firmware.run("fw/gpio.c", on_checkpoint=on_checkpoint)
    assert result.passed, result.output
    seen = [struct.unpack("<I", d)[0] for d in result.data]
    assert seen == [0xBEEF_A5C3, 0x0000_A5C3]
