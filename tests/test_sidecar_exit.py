"""Issue #7: a sidecar (OpenOCD) or the simulator exiting mid-test is reported at once."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from soc_pastel.core import TargetError
from soc_pastel.core.platform import Sidecar


@pytest.fixture
def platform(tmp_path):
    from soc_pastel.core import registry
    from soc_pastel.sim.verilator import VerilatorPlatform

    dev = registry.device("veerwolf")
    p = VerilatorPlatform(dev, dev.variant("sim"), {"run_dir": tmp_path})
    p.sidecars = [Sidecar("on_chip_debug.openocd", ("openocd",), "openocd.log")]
    pipe = SimpleNamespace(exit_status=None)
    p._session = SimpleNamespace(handler=lambda name: pipe, closed=False)
    p._proc = SimpleNamespace(poll=lambda: None)
    p.pipe = pipe
    return p


def test_a_healthy_platform_passes(platform):
    platform.check()


def test_a_dead_sidecar_is_named_with_its_log(platform, tmp_path):
    (tmp_path / "openocd.log").write_text("Error: Failed to read MISA from hart 0\n")
    platform.pipe.exit_status = -11
    with pytest.raises(TargetError) as e:
        platform.check()
    message = str(e.value)
    assert "sidecar 'on_chip_debug.openocd' was killed by signal 11 during the test" in message
    assert str(tmp_path / "openocd.log") in message
    assert "Failed to read MISA" in message  # the log's tail: why it died


def test_an_exited_simulator_is_reported(platform, tmp_path):
    (tmp_path / "sim.log").write_text("%Error: something\n")
    platform._proc = SimpleNamespace(poll=lambda: 3)
    with pytest.raises(TargetError, match="the simulator exited with status 3 during the test"):
        platform.check()


def test_harness_check_covers_time_and_platform():
    from soc_pastel.core import PastelTimeout
    from soc_pastel.core.harness import PlatformHarness

    failed = []
    h = PlatformHarness.__new__(PlatformHarness)
    h._state = "open"
    h._platform = SimpleNamespace(check=lambda: failed.append("platform"))
    h.timeout = SimpleNamespace(check=lambda: None)
    h.check()
    assert failed == ["platform"]

    def timed_out():
        raise PastelTimeout("timed out")

    h.timeout = SimpleNamespace(check=timed_out)
    with pytest.raises(PastelTimeout):
        h.check()  # the time limit first: a stopped platform is its consequence


def test_openocd_gone_reports_its_log(tmp_path):
    from soc_pastel.interfaces.on_chip_debug.openocd.driver import OpenOcd
    from soc_pastel.sim.handlers import PipeClosed

    class Link:
        def request(self, data, timeout_s):
            raise PipeClosed("sidecar 'on_chip_debug.openocd' was killed by signal 11", -11)

    ocd = OpenOcd.__new__(OpenOcd)
    import threading

    ocd._tcl_lock = threading.RLock()
    ocd._link = Link()
    ocd._log_path = Path(tmp_path / "openocd.log")
    ocd._log_path.write_text("Error: unable to halt hart 0\n")
    with pytest.raises(TargetError) as e:
        ocd.tcl("halt")
    assert "OpenOCD stopped (sidecar 'on_chip_debug.openocd' was killed by signal 11)" in str(
        e.value
    )
    assert "unable to halt hart 0" in str(e.value)
