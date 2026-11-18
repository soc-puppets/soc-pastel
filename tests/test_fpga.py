"""The fpga hardware platform."""

import pytest

from soc_pastel.core import ConfigurationError, PlatformKind, registry
from soc_pastel.hardware import FpgaPlatform


@pytest.fixture(autouse=True)
def _fresh():
    FpgaPlatform._programmed.clear()
    yield
    FpgaPlatform._programmed.clear()


def platform(tmp_path, **options):
    dev = registry.device("veerwolf")
    return FpgaPlatform(dev, dev.variant("fpga"), {"run_dir": tmp_path, **options})


def test_registered_and_has_no_simulation_features(tmp_path):
    assert registry.platform("fpga") is FpgaPlatform
    p = platform(tmp_path)
    assert p.kind is PlatformKind.FPGA and p.capabilities() == frozenset()
    assert p.backdoor() is None and p.time_ns() is None
    assert not p.running
    p.start()
    assert p.running
    p.stop()
    assert not p.running


def test_programs_once_per_session(tmp_path):
    marker = tmp_path / "count"
    cmd = f"echo programmed; echo x >> {marker}"
    for n in range(3):
        p = platform(tmp_path / f"run{n}", fpga_program=cmd)
        p.start()
        p.stop()
    assert marker.read_text().count("x") == 1
    assert "programmed" in (tmp_path / "run0" / "program.log").read_text()


def test_failed_programming_is_reported(tmp_path):
    p = platform(tmp_path, fpga_program="echo no cable; exit 3")
    with pytest.raises(ConfigurationError, match="exit status 3(.|\n)*no cable"):
        p.start()
    assert not p.running
