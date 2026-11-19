"""The jlink on-chip debug driver, through each backend: a fake J-Link Commander
(``fake_jlinkexe.py``) and a fake pylink (``fake_pylink.py``)."""

import os
import stat
import sys
from pathlib import Path

import pytest

from soc_pastel.core import ConfigurationError, TargetError, TargetState
from soc_pastel.interfaces.on_chip_debug.jlink import JLink

sys.path.insert(0, str(Path(__file__).parent))
import fake_pylink  # noqa: E402

FAKE_JLINKEXE = Path(__file__).with_name("fake_jlinkexe.py")


class Platform:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.options = {}


class Harness:
    def __init__(self, run_dir):
        self.platform = Platform(run_dir)


@pytest.fixture
def jlinkexe(tmp_path):
    """An executable wrapper around the fake Commander, as JLinkExe would be."""
    exe = tmp_path / "JLinkExe"
    exe.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE_JLINKEXE} "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


@pytest.fixture
def pylink(monkeypatch):
    """The fake pylink, as ``import pylink`` will find it."""
    module = fake_pylink.make_module()
    monkeypatch.setitem(sys.modules, "pylink", module)
    return module


@pytest.fixture(params=["commander", "pylink"])
def backend(request):
    """Each backend in turn, with its fake installed."""
    if request.param == "commander":
        pytest.importorskip("pexpect")
    else:
        request.getfixturevalue("pylink")
    return request.param


def make(tmp_path, exe, backend="commander", **config):
    cfg = {"impl": "jlink", "device": "RISC-V", "backend": backend, "executable": exe, **config}
    return JLink("on_chip_debug", cfg, Harness(tmp_path))


@pytest.fixture
def ocd(tmp_path, jlinkexe, backend):
    j = make(tmp_path, jlinkexe, backend)
    j.open()
    yield j
    j.close()


# -- Both backends ---------------------------------------------------------------------------


def test_connects_and_keeps_a_log(ocd, tmp_path):
    assert "RISC-V" in (tmp_path / "jlink.log").read_text()


def test_reset_halts_and_resume_sets_the_pc(ocd):
    ocd.reset(halt=True)
    assert ocd.state() is TargetState.HALTED and ocd.pc == 0x8000_0000
    ocd.resume(0x8000_0100)
    assert ocd.state() is TargetState.RUNNING
    ocd.halt()
    assert ocd.read_register("pc") == 0x8000_0100
    ocd.step()
    assert ocd.pc == 0x8000_0104
    ocd.reset(halt=False)
    assert ocd.state() is TargetState.RUNNING


def test_registers(ocd):
    ocd.write_register("a0", 0x1234)
    assert ocd.read_register("a0") == 0x1234
    with pytest.raises(TargetError, match="not found"):
        ocd.read_register("nonsense")


def test_memory_small_bulk_and_unaligned(ocd):
    mem = ocd.memory
    mem.write32(0x1000, 0xDEADBEEF)
    assert mem.read32(0x1000) == 0xDEADBEEF
    big = bytes(range(256)) * 4  # through savebin/loadbin, for Commander
    mem.write(0x2000, big)
    assert mem.read(0x2000, len(big)) == big
    mem.write(0x1001, b"\xaa\xbb")  # read-modify-write of a partial word
    assert mem.read(0x1000, 4) == bytes.fromhex("efaabbde")


def test_probe_errors_raise(ocd):
    with pytest.raises(TargetError, match="Could not read memory"):
        ocd.memory.read32(0xF000_0000)


def test_breakpoints(ocd):
    bp = ocd.add_breakpoint(0x8000_0010)
    ocd.remove_breakpoint(bp)
    with pytest.raises(TargetError, match="no breakpoint"):
        ocd.remove_breakpoint(bp)


def test_low_target_voltage_is_explained(tmp_path, jlinkexe, backend, monkeypatch):
    monkeypatch.setenv("FAKE_JLINK_VTREF", "0.000")
    j = make(tmp_path, jlinkexe, backend)
    with pytest.raises(TargetError, match="reference voltage"):
        j.open()
    j.close()


def test_no_jlink_attached_is_explained(tmp_path, jlinkexe, backend, monkeypatch):
    monkeypatch.setenv("FAKE_JLINK_NO_PROBE", "1")
    j = make(tmp_path, jlinkexe, backend)
    with pytest.raises(TargetError, match="can't reach a J-Link on USB.*--jlink-serial"):
        j.open()
    j.close()


def test_config_is_checked(tmp_path, jlinkexe):
    with pytest.raises(ConfigurationError, match="needs 'device'"):
        JLink("ocd", {"impl": "jlink"}, Harness(tmp_path))
    with pytest.raises(ConfigurationError, match="unknown jlink config keys"):
        make(tmp_path, jlinkexe, colour="blue")
    with pytest.raises(ConfigurationError, match="unknown jlink backend 'gdb'.*commander, pylink"):
        make(tmp_path, jlinkexe, "gdb")


# -- Choosing a backend ----------------------------------------------------------------------


def test_jlink_backend_option_overrides_the_binding(tmp_path, jlinkexe, pylink):
    j = make(tmp_path, jlinkexe, "commander")
    j.harness.platform.options["jlink_backend"] = "pylink"
    assert j.backend == "pylink"
    j.open()
    try:
        assert pylink.instances and ("connect", "RISC-V", 4000) in pylink.calls
    finally:
        j.close()


def test_a_bad_jlink_backend_option_is_explained(tmp_path, jlinkexe):
    j = make(tmp_path, jlinkexe)
    j.harness.platform.options["jlink_backend"] = "gdb"
    with pytest.raises(ConfigurationError, match="'gdb' \\(from --jlink-backend\\)"):
        j.open()


# -- Commander only --------------------------------------------------------------------------


def test_commander_log_is_its_output(tmp_path, jlinkexe):
    pytest.importorskip("pexpect")
    j = make(tmp_path, jlinkexe)
    j.open()
    j.close()
    log = (tmp_path / "jlink.log").read_text()
    assert "device RISC-V" in log and "JTAGConf -1 -1" in log and "Connected." in log


def test_a_missing_jlinkexe_is_explained(tmp_path):
    pytest.importorskip("pexpect")
    j = make(tmp_path, os.devnull + "/JLinkExe")
    with pytest.raises((ConfigurationError, TargetError)):
        j.open()
    j.close()


# -- pylink only -----------------------------------------------------------------------------


def test_pylink_selects_the_probe_and_interface(tmp_path, pylink):
    j = make(tmp_path, None, "pylink", serial="123456", interface="SWD", speed=1000)
    j.open()
    j.close()
    assert ("open", 123456, None) in pylink.calls
    assert ("set_tif", "SWD") in pylink.calls and ("connect", "RISC-V", 1000) in pylink.calls
    assert not any(c[0] == "jtag_configure" for c in pylink.calls)
    log = (tmp_path / "jlink.log").read_text()
    assert "> connect(RISC-V, speed=1000)" in log and "Found RISC-V core (fake)" in log


def test_pylink_ip_and_jtag_chain(tmp_path, pylink):
    j = make(tmp_path, None, "pylink", ip="10.0.0.7", jtag_conf="5 1")
    j.open()
    j.close()
    assert ("open", None, "10.0.0.7:19020") in pylink.calls
    assert ("jtag_configure", 5, 1) in pylink.calls


def test_pylink_tunnel(tmp_path, pylink):
    j = make(tmp_path, None, "pylink", ip="tunnel:board7")
    j.open()
    j.close()
    assert ("open", None, "tunnel:board7:19020") in pylink.calls


@pytest.mark.parametrize(
    "ip, addr",
    [
        ("10.0.0.7", "10.0.0.7:19020"),
        ("10.0.0.7:19021", "10.0.0.7:19021"),
        ("tunnel:601012345", "tunnel:601012345:19020"),
        ("tunnel:board7:19021", "tunnel:board7:19021"),
    ],
)
def test_pylink_ip_addr(ip, addr):
    from soc_pastel.interfaces.on_chip_debug.jlink import JLinkPylink

    assert JLinkPylink.ip_addr(ip) == addr


def test_pylink_reset_pin(tmp_path, pylink):
    j = make(tmp_path, None, "pylink", reset_pin=True)
    j.open()
    j.reset()
    assert pylink.instances[0].reset_pin == [0, 1]
    j.close()


def test_pylink_bulk_memory_is_one_call(tmp_path, pylink):
    j = make(tmp_path, None, "pylink")
    j.open()
    j.memory.write(0x2000, bytes(4096))
    j.memory.read(0x2000, 4096)
    j.close()
    assert ("memory_write32", 0x2000, 1024) in pylink.calls
    assert ("memory_read32", 0x2000, 1024) in pylink.calls


def test_pylink_hanging_open_times_out(tmp_path, pylink, monkeypatch):
    monkeypatch.setenv("FAKE_PYLINK_HANG", "1")
    j = make(tmp_path, None, "pylink", timeout_s=0.2)
    with pytest.raises(TargetError, match="did not return from open\\(\\) within 0.2 s"):
        j.open()
    j.close()


def test_pylink_missing_library_is_explained(tmp_path, pylink, monkeypatch):
    monkeypatch.setenv("FAKE_PYLINK_NO_LIBRARY", "1")
    j = make(tmp_path, None, "pylink")
    with pytest.raises(ConfigurationError, match="J-Link library.*PASTEL_JLINK_LIBRARY"):
        j.open()
    j.close()
    j = make(tmp_path, None, "pylink", library=str(tmp_path / "libjlinkarm.so"))
    with pytest.raises(ConfigurationError, match="libjlinkarm.so"):
        j.open()
    j.close()


def test_pylink_not_installed_is_explained(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pylink", None)  # import pylink raises ImportError
    j = make(tmp_path, None, "pylink")
    with pytest.raises(ConfigurationError, match="pylink-square"):
        j.open()
    j.close()
