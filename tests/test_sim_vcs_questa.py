"""The experimental VCS and Questa platforms' build commands and checks (no tools needed)."""

import os

import pytest

from soc_pastel.core import ConfigurationError
from soc_pastel.sim.questa import QuestaImage, questa_include_dir
from soc_pastel.sim.sources import SimSources
from soc_pastel.sim.vcs import VcsImage

SOURCES = SimSources(
    top="tb",
    args=("/src/tb.sv", "+incdir+/src/inc", "-DFAST=1", "/src/tb.vlt"),
    tool_args={"vcs": ("-assert", "svaext"), "questa": ("-mfcu",)},
)


def test_vcs_command(tmp_path):
    img = VcsImage(SOURCES, tmp_path, "vcs", waves=True)
    cmd = img.command()
    assert cmd[:3] == ["vcs", "-full64", "-sverilog"] and cmd[cmd.index("-top") + 1] == "tb"
    assert cmd[cmd.index("-o") + 1] == str(img.exe) and img.exe.name == "simv"
    assert {"-debug_access+r", "+define+PASTEL_WAVES", "+define+FAST=1"} <= set(cmd)
    assert "/src/tb.vlt" not in cmd and "svaext" in cmd and "-mfcu" not in cmd
    assert cmd[-2] == "-CFLAGS" and "-std=c++17" in cmd[-1]
    assert any(c.endswith("pastel_ep.cpp") for c in cmd)  # VCS compiles the DPI C++ itself


def test_vcs_errors_fail_the_build(tmp_path):
    rules = VcsImage(SOURCES, tmp_path, "vcs").build_rules
    warned = rules.check_text("Warning-[TFIPC] Too few instance port connections\n")
    assert not warned.failed and len(warned.warnings) == 1
    assert rules.check_text("Parsing design file\nError-[SE] Syntax error\n").failed


@pytest.fixture
def questa_install(tmp_path, monkeypatch):
    bindir = tmp_path / "questa" / "bin"
    bindir.mkdir(parents=True)
    for tool in ("vsim", "vlog", "vopt", "vlib"):
        (bindir / tool).write_text("#!/bin/sh\n")
        (bindir / tool).chmod(0o755)
    (tmp_path / "questa" / "include").mkdir()
    (tmp_path / "questa" / "include" / "svdpi.h").write_text("")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("QUESTA_HOME", raising=False)
    monkeypatch.delenv("MTI_HOME", raising=False)
    return bindir


def test_questa_steps_use_one_installation(tmp_path, questa_install):
    img = QuestaImage(SOURCES, tmp_path / "w", "vsim", waves=True)
    vlib, vlog, vopt, dpi = img.steps()
    assert vlib == [str(questa_install / "vlib"), str(img.work)]
    assert vlog[0] == str(questa_install / "vlog") and "-sv" in vlog and "-mfcu" in vlog
    assert "+define+FAST=1" in vlog and "+define+PASTEL_WAVES" in vlog
    assert vopt[0] == str(questa_install / "vopt") and "+acc" in vopt
    assert vopt[-3:] == ["tb", "-o", QuestaImage.OPTIMISED]
    assert f"-I{questa_install.parent / 'include'}" in dpi
    assert questa_include_dir("vsim") == questa_install.parent / "include"


def test_questa_run_script(tmp_path, questa_install):
    img = QuestaImage(SOURCES, tmp_path / "w", "vsim")
    img.dir.mkdir(parents=True)
    img.finish()
    text = img.exe.read_text()
    assert img.is_built() and os.access(img.exe, os.X_OK)
    assert "-onfinish exit" in text and "run -all; quit -f" in text and '"$@"' in text
    assert f"-sv_lib {img.dpi.with_suffix('')}" in text  # vsim adds .so itself


def test_questa_errors_fail_the_build(tmp_path):
    rules = QuestaImage(SOURCES, tmp_path, "vsim").build_rules
    assert not rules.check_text("** Warning: tb.sv(4): (vlog-2600) unused\n").failed
    assert rules.check_text("** Error: tb.sv(3): near ';': syntax error\n").failed
    assert rules.check_text("** Fatal: (vopt-1) top not found\n").failed


def test_missing_questa_svdpi_is_explained(monkeypatch):
    monkeypatch.delenv("QUESTA_HOME", raising=False)
    monkeypatch.delenv("MTI_HOME", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(ConfigurationError, match="QUESTA_HOME"):
        questa_include_dir("vsim")


def test_keys_differ_between_simulators(tmp_path):
    assert VcsImage(SOURCES, tmp_path, "vcs").key != QuestaImage(SOURCES, tmp_path, "vsim").key
