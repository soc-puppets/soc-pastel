"""The Xcelium platform's build commands and checks (no Xcelium needed)."""

import os

import pytest

from soc_pastel.core import ConfigurationError
from soc_pastel.sim.sources import SimSources
from soc_pastel.sim.xcelium import (
    SNAPSHOT_WRITTEN,
    XceliumImage,
    xcelium_include_dir,
    xcelium_source_args,
)

SOURCES = SimSources(
    top="tb",
    args=("/src/tb.sv", "+incdir+/src/inc", "-DFAST=1", "/src/tb.vlt"),
    tool_args={"xcelium": ("-uvm",), "verilator": ("--timing",)},
)


@pytest.fixture
def xcelium_home(tmp_path, monkeypatch):
    inc = tmp_path / "xcelium" / "tools" / "include"
    inc.mkdir(parents=True)
    (inc / "svdpi.h").write_text("")
    monkeypatch.setenv("XCELIUM_HOME", str(tmp_path / "xcelium"))
    monkeypatch.setenv("PATH", os.environ["PATH"])  # no xmroot needed
    return inc


def image(tmp_path, **kw):
    return XceliumImage(SOURCES, tmp_path / "work", "xrun", **kw)


def test_source_args_are_translated():
    assert xcelium_source_args(SOURCES.args) == ["/src/tb.sv", "+incdir+/src/inc", "+define+FAST=1"]


def test_elaboration_command(tmp_path):
    cmd = image(
        tmp_path, build_prefix=["srun", "-c", "8"], extra_args=("-assert",)
    ).elaborate_command()
    assert cmd[:4] == ["srun", "-c", "8", "xrun"]
    assert "-elaborate" in cmd and cmd[cmd.index("-top") + 1] == "tb"
    assert "-uvm" in cmd and "-assert" in cmd and "--timing" not in cmd  # only xcelium's tool_args
    assert "+define+FAST=1" in cmd and "/src/tb.vlt" not in cmd
    assert "-access" not in cmd
    waves = image(tmp_path, waves=True)
    assert waves.key.endswith("-waves")
    assert {"-access", "+r", "+define+PASTEL_WAVES"} <= set(waves.elaborate_command())


def test_dpi_library_uses_xceliums_svdpi(tmp_path, xcelium_home):
    cmd = image(tmp_path).dpi_command()
    assert f"-I{xcelium_home}" in cmd and cmd[-2:] == ["-o", str(image(tmp_path).dpi)]
    assert xcelium_include_dir() == xcelium_home


def test_missing_svdpi_is_explained(monkeypatch):
    monkeypatch.delenv("XCELIUM_HOME", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(ConfigurationError, match="XCELIUM_HOME"):
        xcelium_include_dir()


def test_elaboration_output_is_checked(tmp_path):
    rules = image(tmp_path).build_rules
    assert not rules.check_text(f"xmelab ...\n{SNAPSHOT_WRITTEN}: worklib.tb:sv\n").failed
    bad = rules.check_text(f"xmelab: *E,MULAXX (tb.sv,3|5): bad\n{SNAPSHOT_WRITTEN}\n")
    assert bad.failed and "*E,MULAXX" in bad.describe()
    missing = rules.check_text("xrun: exiting\n")
    assert missing.failed and missing.missing == [SNAPSHOT_WRITTEN]
    warned = rules.check_text(f"xmelab: *W,CUVWSP (tb.sv,1|2): port\n{SNAPSHOT_WRITTEN}\n")
    assert not warned.failed and len(warned.warnings) == 1
    echoed = rules.check_text(f"$ xrun -nowarn *E,FOO\n{SNAPSHOT_WRITTEN}\n")
    assert not echoed.failed  # PASTEL's own command echo is never an error


def test_run_script(tmp_path):
    img = image(tmp_path)
    img.dir.mkdir(parents=True)
    img.finish()
    text = img.exe.read_text()
    assert img.is_built() and os.access(img.exe, os.X_OK)
    assert "mkdir -p .cds_tmp" in text and '"$@"' in text
    assert f"-sv_lib {img.dpi}" in text and "-R" in text


def test_local_xrun_needed_only_without_a_build_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(ConfigurationError, match="xrun not found"):
        image(tmp_path).check_tool()
    image(tmp_path, build_prefix=["srun"]).check_tool()


def test_key_differs_from_verilators(tmp_path):
    from soc_pastel.sim.verilator import VerilatorImage

    assert image(tmp_path).key != VerilatorImage(SOURCES, tmp_path, "verilator", 1).key
    assert image(tmp_path, extra_args=("-x",)).key != image(tmp_path).key


def _verilator_include():
    import shutil
    import subprocess

    exe = shutil.which("verilator")
    if not exe or not shutil.which("g++"):
        return None
    root = subprocess.run([exe, "--getenv", "VERILATOR_ROOT"], capture_output=True, text=True)
    inc = os.path.join(root.stdout.strip(), "include", "vltstd")
    return inc if os.path.isfile(os.path.join(inc, "svdpi.h")) else None


@pytest.mark.skipif(_verilator_include() is None, reason="needs g++ and an svdpi.h (Verilator's)")
@pytest.mark.parametrize("snapshot", [True, False])
def test_build_runs_every_step_then_writes_the_run_script(tmp_path, monkeypatch, snapshot):
    home = tmp_path / "xcelium"
    (home / "tools").mkdir(parents=True)
    (home / "tools" / "include").symlink_to(_verilator_include())
    monkeypatch.setenv("XCELIUM_HOME", str(home))
    fake = tmp_path / "bin" / "xrun"
    fake.parent.mkdir()
    line = SNAPSHOT_WRITTEN if snapshot else "nothing written"
    fake.write_text(f'#!/bin/sh\necho "xrun $*"\necho "{line}"\n')
    fake.chmod(0o755)
    img = XceliumImage(SOURCES, tmp_path / "work", str(fake))
    if snapshot:
        img.build()
        assert img.is_built() and img.dpi.is_file()
        log = img.build_log.read_text()
        assert log.index("-shared") < log.index("-elaborate")  # both steps, in order
    else:
        with pytest.raises(ConfigurationError, match="reported errors"):
            img.build()
        assert not img.is_built()
