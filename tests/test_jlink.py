"""The jlink on-chip debug driver, against a fake J-Link Commander."""

import os
import stat
import sys
from pathlib import Path

import pytest

from soc_pastel.core import ConfigurationError, TargetError, TargetState

pytest.importorskip("pexpect")

from soc_pastel.interfaces.on_chip_debug.jlink import JLink  # noqa: E402

FAKE = Path(__file__).with_name("fake_jlinkexe.py")


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
    exe.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def make(tmp_path, jlinkexe, **config):
    cfg = {"impl": "jlink", "device": "RISC-V", "executable": jlinkexe, **config}
    return JLink("on_chip_debug", cfg, Harness(tmp_path))


@pytest.fixture
def ocd(tmp_path, jlinkexe):
    j = make(tmp_path, jlinkexe)
    j.open()
    yield j
    j.close()


def test_connects_and_keeps_a_log(ocd, tmp_path):
    log = (tmp_path / "jlink.log").read_text()
    assert "device RISC-V" in log and "JTAGConf -1 -1" in log and "Connected." in log


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
    big = bytes(range(256)) * 4  # through savebin/loadbin
    mem.write(0x2000, big)
    assert mem.read(0x2000, len(big)) == big
    mem.write(0x1001, b"\xaa\xbb")  # read-modify-write of a partial word
    assert mem.read(0x1000, 4) == bytes.fromhex("efaabbde")


def test_commander_errors_raise(ocd):
    with pytest.raises(TargetError, match="Could not read memory"):
        ocd.memory.read32(0xF000_0000)


def test_breakpoints(ocd):
    bp = ocd.add_breakpoint(0x8000_0010)
    ocd.remove_breakpoint(bp)
    with pytest.raises(TargetError, match="no breakpoint"):
        ocd.remove_breakpoint(bp)


def test_low_target_voltage_is_explained(tmp_path, jlinkexe, monkeypatch):
    monkeypatch.setenv("FAKE_JLINK_VTREF", "0.000")
    j = make(tmp_path, jlinkexe)
    with pytest.raises(TargetError, match="reference voltage"):
        j.open()
    j.close()


def test_config_is_checked(tmp_path, jlinkexe):
    with pytest.raises(ConfigurationError, match="needs 'device'"):
        JLink("ocd", {"impl": "jlink"}, Harness(tmp_path))
    with pytest.raises(ConfigurationError, match="unknown jlink config keys"):
        make(tmp_path, jlinkexe, colour="blue")


def test_a_missing_jlinkexe_is_explained(tmp_path):
    j = make(tmp_path, os.devnull + "/JLinkExe")
    with pytest.raises((ConfigurationError, TargetError)):
        j.open()
    j.close()


def test_no_jlink_attached_is_explained(tmp_path, jlinkexe, monkeypatch):
    monkeypatch.setenv("FAKE_JLINK_NO_PROBE", "1")
    j = make(tmp_path, jlinkexe)
    with pytest.raises(TargetError, match="can't reach a J-Link on USB.*--jlink-serial"):
        j.open()
    j.close()
