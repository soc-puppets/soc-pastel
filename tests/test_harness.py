from typing import ClassVar

import pytest

from soc_pastel.core import (
    BACKDOOR,
    CapabilityError,
    CodeLocation,
    CpuSpec,
    Device,
    InterfaceBinding,
    MemoryInterface,
    MemoryRegion,
    OnChipDebug,
    Platform,
    PlatformHarness,
    PlatformKind,
    TargetState,
    Variant,
)
from soc_pastel.interfaces.spi import SpiEndpoint

EVENTS = []


class FakeMemory(MemoryInterface):
    def __init__(self):
        self.mem = bytearray(0x100)

    def read(self, addr, length):
        return bytes(self.mem[addr : addr + length])

    def write(self, addr, data):
        self.mem[addr : addr + len(data)] = data


class FakeOcd(OnChipDebug):
    impl_name: ClassVar[str] = "fake"
    _mem = FakeMemory()

    def open(self):
        EVENTS.append("open ocd")

    def close(self):
        EVENTS.append("close ocd")

    def reset(self, *, halt=True):
        pass

    def halt(self):
        pass

    def resume(self, pc=None):
        pass

    def step(self):
        pass

    def state(self):
        return TargetState.HALTED

    def read_register(self, name):
        return 0x1234

    def write_register(self, name, value):
        pass

    @property
    def memory(self):
        return self._mem

    def add_breakpoint(self, addr, *, hardware=True):
        return 0

    def remove_breakpoint(self, breakpoint_id):
        pass


class FakeSpi(SpiEndpoint):
    impl_name: ClassVar[str] = "fake"

    def open(self):
        EVENTS.append(f"open {self.name}")

    def close(self):
        EVENTS.append(f"close {self.name}")

    def configure(self, *, mode=0, bits=8, cs=0):
        pass

    def load_response(self, data):
        pass

    def captured(self):
        return []

    def clear(self):
        pass


class FakePlatform(Platform):
    name = "fake"
    kind = PlatformKind.SIM
    backdoor_mem = FakeMemory()

    def capabilities(self):
        return frozenset({BACKDOOR})

    def start(self):
        EVENTS.append("start")

    def stop(self):
        EVENTS.append("stop")

    @property
    def running(self):
        return True

    def backdoor(self):
        return self.backdoor_mem


DEVICE = Device(
    name="d",
    cpu=CpuSpec(family="x", isa="rv32i", abi="ilp32", reset_vector=0),
    variants={
        "v": Variant(
            name="v",
            regions=(MemoryRegion(name="ram", base=0, size=0x100),),
            code_locations=(CodeLocation(name="ram", text="ram", data="ram"),),
        )
    },
    default_variant="v",
    interfaces={
        "on_chip_debug": InterfaceBinding(type="on_chip_debug", impls={"sim": {"impl": "fake"}})
    },
)


def make(**extra):
    ifaces = {"on_chip_debug": (FakeOcd, {"impl": "fake"}), **extra}
    return PlatformHarness(
        DEVICE, DEVICE.variant(), FakePlatform(DEVICE, DEVICE.variant(), {}), ifaces
    )


def test_capabilities_combine_platform_and_interfaces():
    h = make(spi0=(FakeSpi, {"impl": "fake"}))
    assert h.capabilities == {"backdoor", "on_chip_debug", "spi0"}
    assert h.has("spi0", "backdoor")
    with pytest.raises(CapabilityError, match="uart0"):
        h.require("uart0")


def test_iface_type_check():
    h = make(spi0=(FakeSpi, {"impl": "fake"}))
    assert isinstance(h.iface("spi0", SpiEndpoint), FakeSpi)
    with pytest.raises(CapabilityError):
        h.iface("on_chip_debug", SpiEndpoint)
    assert h.ocd.pc == 0x1234


def test_lifecycle_order():
    EVENTS.clear()
    with make(spi0=(FakeSpi, {"impl": "fake"})):
        pass
    assert EVENTS == ["start", "open ocd", "open spi0", "close spi0", "close ocd", "stop"]


def test_memory_prefers_backdoor():
    h = make()
    assert h.memory() is FakePlatform.backdoor_mem
    assert h.memory(prefer_backdoor=False) is FakeOcd._mem
    h.memory().write32(0x10, 0xDEADBEEF)
    assert h.memory().read(0x10, 4) == bytes.fromhex("efbeadde")


def test_regs_absent_raises():
    with pytest.raises(CapabilityError, match="register model"):
        _ = make().regs


# --- Endpoint-backed interfaces ---------------------------------------------------------

from soc_pastel.core import EndpointBacked, EndpointHandler, SimulationPlatform  # noqa: E402


class SpiVipHandler(EndpointHandler):
    type_name = "spi"

    def on_message(self, payload):
        pass


class SimSpi(EndpointBacked, FakeSpi):
    impl_name: ClassVar[str] = "sim"
    handler_class = SpiVipHandler


class FakeSim(SimulationPlatform):
    name = "fakesim"
    seen = None

    def image(self):
        raise NotImplementedError

    @property
    def endpoints(self):
        raise NotImplementedError

    def start(self):
        FakeSim.seen = dict(self.expected_endpoints)
        FakeSim.sidecars_seen = list(self.sidecars)

    def stop(self):
        pass

    @property
    def running(self):
        return True


def test_sim_platform_told_which_endpoints_to_expect():
    v = DEVICE.variant()
    ifaces = {
        "on_chip_debug": (FakeOcd, {"impl": "fake"}),
        "spi0": (SimSpi, {"impl": "sim"}),
        "spi1": (FakeSpi, {"impl": "fake"}),  # not endpoint-backed: not expected
    }
    with PlatformHarness(DEVICE, v, FakeSim(DEVICE, v, {}), ifaces):
        pass
    assert set(FakeSim.seen) == {"spi0"}
    assert FakeSim.seen["spi0"].type == "spi"
    assert FakeSim.seen["spi0"].handler_class is SpiVipHandler


def test_sim_platform_given_interface_sidecars_before_start(tmp_path):
    from soc_pastel.core import Sidecar
    from soc_pastel.interfaces.on_chip_debug.openocd import OpenOcd

    class SidecarSpi(FakeSpi):
        impl_name: ClassVar[str] = "sidecar"

        def sim_sidecars(self):
            return [Sidecar("helper", ("helper", "--serve"), "helper.log")]

    v = DEVICE.variant()
    ocd_cfg = {"impl": "openocd", "transport": "remote_bitbang", "cpu": "veer", "chain": "x"}
    ifaces = {"on_chip_debug": (OpenOcd, ocd_cfg), "spi0": (SidecarSpi, {"impl": "sidecar"})}
    platform = FakeSim(DEVICE, v, {"run_dir": tmp_path})
    harness = PlatformHarness(DEVICE, v, platform, ifaces)
    with pytest.raises(NotImplementedError):  # OpenOCD reaches for the endpoints on open
        harness.open()
    names = [sc.name for sc in FakeSim.sidecars_seen]
    assert names == ["on_chip_debug.openocd", "helper"]
    ocd = FakeSim.sidecars_seen[0]
    assert ocd.argv[1:] == ("-f", "openocd.cfg") and ocd.log == "openocd.log"
    cfg = (tmp_path / "openocd.cfg").read_text()
    assert "tcl_port pipe" in cfg
    assert "gdb_port disabled" in cfg and "telnet_port disabled" in cfg
    assert "remote_bitbang host $::env(PASTEL_JTAG_SOCKET)" in cfg
