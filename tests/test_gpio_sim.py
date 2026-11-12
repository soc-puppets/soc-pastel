"""The gpio/sim host side against a fake pastel_gpio agent speaking its protocol."""

import struct

import pytest

from soc_pastel.core import ConfigurationError, SimulationPlatform
from soc_pastel.core.endpoints import EndpointInfo
from soc_pastel.interfaces.gpio.sim import GpioAgent, SimGpio


class FakeAgent:
    """pastel_gpio with 16 pins; pins 3:0 are X (nobody drives them, 4-state)."""

    WIDTH, X_PINS = 16, 0x000F

    def __init__(self):
        self.drive = self.enable = 0
        self.requests = []
        self.handler = GpioAgent(EndpointInfo(0, "gpio", "gpio", "tb.u_gpio"), self.receive)

    def receive(self, payload):
        op, seq = struct.unpack_from("<BI", payload)
        fields = struct.unpack_from(f"<{(len(payload) - 5) // 8}Q", payload, 5)
        self.requests.append((op, fields))
        if op == GpioAgent.DRIVE:
            value, mask = fields
            self.enable |= mask
            self.drive = (self.drive & ~mask) | (value & mask)
        elif op == GpioAgent.RELEASE:
            self.enable &= ~fields[0]
        unknown = self.X_PINS & ~self.enable
        pins = self.drive & self.enable
        reply = struct.pack("<BIBQQ", op | 0x80, seq, self.WIDTH, pins, unknown)
        self.handler.on_message(reply)


class FakeSim(SimulationPlatform):
    name = "fakesim"

    def __init__(self, handler):
        self._handler = handler

    def image(self):
        raise NotImplementedError

    @property
    def endpoints(self):
        return self

    def handler(self, name):
        return self._handler

    def start(self):
        pass

    def stop(self):
        pass

    @property
    def running(self):
        return True


class Harness:
    def __init__(self, platform):
        self.platform = platform


@pytest.fixture
def agent():
    return FakeAgent()


@pytest.fixture
def gpio(agent):
    return SimGpio("gpio", {"impl": "sim"}, Harness(FakeSim(agent.handler)))


def test_width_is_learnt_from_the_agent(gpio, agent):
    assert gpio.width == 16
    assert gpio.width == 16
    assert len(agent.requests) == 1  # asked once


def test_drive_sends_value_under_mask_and_release(gpio, agent):
    gpio.drive(0xFFFF, 0x00F0)
    assert agent.requests[-1] == (GpioAgent.DRIVE, (0x00F0, 0x00F0))  # value is masked
    assert gpio.sample() == 0x00F0
    gpio.release(0x0030)
    assert agent.requests[-1] == (GpioAgent.RELEASE, (0x0030,))
    assert gpio.sample() == 0x00C0


def test_unknown_pins_sample_as_zero_and_are_reported(gpio):
    assert gpio.sample() == 0
    assert gpio.sample_unknown() == 0x000F
    gpio.drive(0x0005, 0x0003)
    assert gpio.sample() == 0x0001
    assert gpio.sample_unknown() == 0x000C


def test_masks_beyond_the_pins_are_rejected(gpio):
    with pytest.raises(ValueError, match="beyond its 16"):
        gpio.drive(0, 1 << 16)
    with pytest.raises(ValueError):
        gpio.release(-1)


def test_unknown_config_keys_are_rejected():
    with pytest.raises(ConfigurationError, match="unknown gpio/sim config"):
        SimGpio("gpio", {"impl": "sim", "width": 16}, Harness(None))
