"""Test timeouts in chip time: the watchdog, chip speed and settings."""

import time
from types import SimpleNamespace

import pytest

from soc_pastel.core import PastelTimeout
from soc_pastel.core.device import (
    CodeLocation,
    CpuSpec,
    Device,
    InterfaceBinding,
    MemoryRegion,
    Variant,
)
from soc_pastel.core.errors import ConfigurationError
from soc_pastel.core.timeout import TimeoutSettings, Watchdog


def device(chip_mhz=None, variant_mhz=None):
    return Device(
        name="d",
        cpu=CpuSpec(family="x", isa="rv32i", abi="ilp32", reset_vector=0),
        variants={
            "fpga": Variant(
                name="fpga",
                regions=(MemoryRegion(name="ram", base=0, size=0x100),),
                code_locations=(CodeLocation(name="ram", text="ram", data="ram"),),
                clock_mhz=variant_mhz,
            )
        },
        default_variant="fpga",
        interfaces={
            "on_chip_debug": InterfaceBinding(type="on_chip_debug", impls={"sim": {"impl": "x"}})
        },
        clock_mhz=chip_mhz,
    )


class Platform:
    """Simulation time set by the test (None: a hardware platform, wall-clock time)."""

    def __init__(self, sim_ns=None):
        self.sim_ns = sim_ns
        self.stopped = False

    def time_ns(self):
        return self.sim_ns

    def stop(self):
        self.stopped = True


def watchdog(settings, platform, dev=None, monkeypatch=None):
    dev = dev or device()
    harness = SimpleNamespace(device=dev, variant=dev.variant(), _platform=platform)
    w = Watchdog(harness)
    w.POLL_S = 0.01
    w.configure(settings)
    return w


def wait_for(condition, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def test_chip_speed():
    assert device().chip_speed(device().variant()) == 1.0
    assert device(1000).chip_speed(device(1000).variant()) == 1.0  # the variant runs at speed
    d = device(1000, 20)
    assert d.chip_speed(d.variant()) == pytest.approx(0.02)
    d = device(1000, 12.5)  # non-integer clocks
    assert d.chip_speed(d.variant()) == pytest.approx(0.0125)


def test_clocks_must_be_positive():
    with pytest.raises(ConfigurationError, match="device 'd': clock_mhz must be positive"):
        device(0)
    with pytest.raises(ConfigurationError, match="variant 'fpga': clock_mhz must be positive"):
        device(1000, -5)


def test_simulation_times_out_after_chip_time_plus_grace():
    sim = Platform(sim_ns=1_000_000)
    w = watchdog(TimeoutSettings(chip_ms=2, grace_s=0.2), sim)
    w.start()
    sim.sim_ns += 1_500_000  # 1.5 ms of chip time: within the limit
    time.sleep(0.3)
    assert w.expired is None
    sim.sim_ns += 1_000_000  # 2.5 ms: over it; the grace starts
    time.sleep(0.05)
    assert w.expired is None
    assert wait_for(lambda: w.expired is not None)
    assert "more than 2 ms of chip time, plus 0.2 s grace" in w.expired
    assert wait_for(lambda: sim.stopped)
    with pytest.raises(PastelTimeout, match="2 ms of chip time"):
        w.check()
    w.stop()


def test_a_slow_variant_gets_longer():
    hw = Platform()  # wall-clock time
    w = watchdog(TimeoutSettings(chip_ms=2, grace_s=0), hw, device(1000, 20))  # 50 times slower
    began = time.monotonic()
    w.start()
    assert wait_for(lambda: w.expired is not None)
    took = time.monotonic() - began
    assert 0.09 <= took < 2, took  # 2 ms of chip time is 100 ms at 20 MHz
    assert "(100 ms at variant 'fpga''s 20 MHz)" in w.expired


def test_restart_gives_a_new_budget():
    sim = Platform(sim_ns=0)
    w = watchdog(TimeoutSettings(chip_ms=1, grace_s=0.1), sim)
    w.start()
    sim.sim_ns = 900_000
    time.sleep(0.05)
    w.restart(chip_ms=5)  # from 0.9 ms: until 5.9 ms
    sim.sim_ns = 5_000_000
    time.sleep(0.3)
    assert w.expired is None
    sim.sim_ns = 6_000_000
    assert wait_for(lambda: w.expired is not None)
    assert "5 ms of chip time" in w.expired
    w.stop()


def test_stuck_simulation_times_out():
    sim = Platform(sim_ns=42_000_000)
    w = watchdog(TimeoutSettings(stall_s=0.2), sim)
    w.start()
    assert wait_for(lambda: w.expired is not None)
    assert "simulation time stopped at 42.000 ms for 0.2 s" in w.expired
    assert wait_for(lambda: sim.stopped)  # the reason is recorded first, then it stops


def test_advancing_simulation_is_not_stuck():
    sim = Platform(sim_ns=0)
    w = watchdog(TimeoutSettings(stall_s=0.2), sim)
    w.start()
    for _ in range(40):
        sim.sim_ns += 1000
        time.sleep(0.01)
    assert w.expired is None
    w.stop()


def test_no_limits_no_thread():
    w = watchdog(TimeoutSettings(), Platform(sim_ns=0))
    w.start()
    assert w._thread is None
    w.check()  # never raises


def test_a_vanished_platform_ends_the_watch_quietly():
    class Gone(Platform):
        def time_ns(self):
            raise ConnectionError("simulator closed")

    w = watchdog(TimeoutSettings(chip_ms=1), Platform(sim_ns=0))
    w.start()
    w._harness._platform = Gone()
    time.sleep(0.1)
    assert w.expired is None and not w._thread.is_alive()


# -- Settings ------------------------------------------------------------------------------


class Item:
    def __init__(self, ini=None, marker=None, no_timeout=False):
        defaults = {
            "pastel_timeout_chip_ms": "100",
            "pastel_timeout_grace_s": "10",
            "pastel_timeout_stall_s": "600",
        }
        ini = {**defaults, **(ini or {})}
        self.config = SimpleNamespace(getini=ini.__getitem__, getoption=lambda name: no_timeout)
        self._marker = marker
        self.nodeid = "test_x"

    def get_closest_marker(self, name):
        return self._marker


def test_settings_defaults_ini_and_marker():
    from soc_pastel.pytest_plugin import timeout_settings

    assert timeout_settings(Item()) == TimeoutSettings(chip_ms=100, grace_s=10, stall_s=600)
    assert timeout_settings(Item(ini={"pastel_timeout_chip_ms": "250.5"})).chip_ms == 250.5
    assert timeout_settings(Item(ini={"pastel_timeout_stall_s": "none"})).stall_s is None
    marker = SimpleNamespace(args=(), kwargs={"chip_ms": 5000, "grace_s": 30})
    s = timeout_settings(Item(marker=marker))
    assert (s.chip_ms, s.grace_s, s.stall_s) == (5000, 30, 600)
    unlimited = SimpleNamespace(args=(), kwargs={"chip_ms": None})
    assert timeout_settings(Item(marker=unlimited)).chip_ms is None
    assert not timeout_settings(Item(no_timeout=True)).active


def test_bad_settings_are_explained():
    from soc_pastel.pytest_plugin import timeout_settings

    with pytest.raises(pytest.UsageError, match="pastel_timeout_chip_ms: expected a number"):
        timeout_settings(Item(ini={"pastel_timeout_chip_ms": "lots"}))
    bad = SimpleNamespace(args=(), kwargs={"wall_s": 5})
    with pytest.raises(pytest.UsageError, match="takes chip_ms=, grace_s= and stall_s="):
        timeout_settings(Item(marker=bad))
