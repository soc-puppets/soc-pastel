"""The Xcelium simulation platform (``--platform=xcelium``).

An image is an elaborated snapshot, PASTEL's DPI library and a small ``run``
script that calls ``xrun -R`` on them. PASTEL launches the script as it would
a Verilator binary, so every launcher works (see :mod:`soc_pastel.sim.launch`).

Elaboration runs under ``--xcelium-build-prefix`` if given, e.g. ``srun`` to
elaborate on a compute farm.

Waves (``--waves``) are recorded by PASTEL's ``pastel_server`` as a SimVision
database: ``sim.trn`` with ``sim.dsn`` beside it (``$recordfile`` keeps the
base name and adds those suffixes). Xcelium can pause recording, so pausing
waves from tests and firmware works.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from soc_pastel.core.errors import ConfigurationError

from .simulator import PASTEL_CPP, PASTEL_HDL, SIM_CSRC, SIM_HW, SimulatorImage, SimulatorPlatform
from .sources import SimSources

#: An Xcelium error or fatal message, e.g. ``xmelab: *E,MULAXX (...)``.
XRUN_ERROR = re.compile(r"\*[EF],[A-Z0-9_]+")
#: What xmelab prints once it has written the snapshot that ``xrun -R`` runs.
SNAPSHOT_WRITTEN = "Writing initial simulation snapshot"


def xcelium_source_args(args: Sequence[str]) -> list[str]:
    """Translate simulator-independent source args for xrun.

    ``.vlt`` files (Verilator configuration) are dropped,
    ``-D<name>[=<value>]`` becomes ``+define+<name>[=<value>]``, and files and
    ``+incdir+`` pass through.
    """
    out = []
    for arg in args:
        if arg.endswith(".vlt"):
            continue
        out.append("+define+" + arg[2:] if arg.startswith("-D") else arg)
    return out


def xcelium_include_dir() -> Path:
    """The directory holding Xcelium's ``svdpi.h``, from ``$XCELIUM_HOME`` or ``xmroot``."""
    roots = []
    if os.environ.get("XCELIUM_HOME"):
        roots.append(Path(os.environ["XCELIUM_HOME"]))
    if shutil.which("xmroot"):
        out = subprocess.run(["xmroot"], capture_output=True, text=True).stdout.strip()
        if out:
            roots.append(Path(out))
    for root in roots:
        for sub in ("tools/include", "tools/inca/include"):
            if (root / sub / "svdpi.h").is_file():
                return root / sub
    raise ConfigurationError(
        "cannot find Xcelium's svdpi.h: set XCELIUM_HOME or put xmroot on PATH"
    )


class XceliumImage(SimulatorImage):
    """An elaborated Xcelium snapshot, PASTEL's DPI library and a run script.

    :attr:`exe` is the run script, written last, so its presence means the
    image is complete.

    Args:
        build_prefix: Command the elaboration runs under, e.g. ``["srun"]``.
    """

    tool: ClassVar[str] = "xcelium"
    TOOL_ENV: ClassVar[tuple[str, ...]] = (*SimulatorImage.TOOL_ENV, "XCELIUM_HOME")

    def __init__(
        self,
        sources: SimSources,
        workdir: Path,
        xrun: str,
        *,
        build_prefix: Sequence[str] = (),
        waves: bool = False,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.build_prefix = list(build_prefix)
        super().__init__(sources, workdir, xrun, waves=waves, extra_args=extra_args)
        self.lib = self.dir / "xcelium.d"
        self.dpi = self.dir / "libpastel_dpi.so"

    @property
    def exe(self) -> Path:  # noqa: D102
        return self.dir / "run"

    def check_tool(self) -> None:
        """Elaborating elsewhere (``--xcelium-build-prefix``) needs no local xrun."""
        if not self.build_prefix:
            super().check_tool()

    def dpi_command(self) -> list[str]:
        """Compile PASTEL's DPI C++ into a shared library for ``-sv_lib``."""
        return [
            os.environ.get("CXX", "g++"), "-shared", "-fPIC", "-O2", "-std=c++17",
            f"-I{SIM_CSRC}", f"-I{xcelium_include_dir()}",
            *map(str, PASTEL_CPP),
            "-o", str(self.dpi),
        ]  # fmt: skip

    def elaborate_command(self) -> list[str]:
        """Elaborate the testbench into a snapshot in :attr:`lib`."""
        # No -cds_implicit_tmpdir here: it also moves the implicit cds.lib
        # that maps worklib to the snapshot library, so xrun -R, run elsewhere
        # with its own tmpdir, would not find the snapshot (*F,NOSNAP).
        return [
            *self.build_prefix,
            self.executable,
            "-elaborate", "-64bit", "-sv", "-licqueue",
            "-timescale", "1ns/1ps",
            "-disable_sem2009",
            "-nowarn", "ENUMERR",
            "-top", self.sources.top,
            "-xmlibdirname", str(self.lib),
            "-l", str(self.dir / "xrun_elab.log"),
            *(["-access", "+r", "+define+PASTEL_WAVES"] if self.waves else []),
            *self.extra_args,
            f"+incdir+{SIM_HW}",
            *map(str, PASTEL_HDL),
            *xcelium_source_args(self.sources.args),
        ]  # fmt: skip

    def run_command(self) -> list[str]:
        """What the run script executes; plusargs are appended."""
        return [
            self.executable,
            "-R", "-64bit", "-unbuffered", "-licqueue",
            "-xmlibdirname", str(self.lib),
            "-cds_implicit_tmpdir", ".cds_tmp",
            "-l", "xrun.log",
            "-sv_lib", str(self.dpi),
        ]  # fmt: skip

    def steps(self) -> list[list[str]]:  # noqa: D102
        return [self.dpi_command(), self.elaborate_command()]

    def check_step(self, index: int, output: str) -> None:
        """Elaboration must report no errors and must have written the snapshot.

        The exit status alone isn't trusted: a batch wrapper may not pass it
        on, and a missing snapshot only shows up later as ``*F,NOSNAP``.
        """
        if index != 1:
            return
        errors = [ln.strip() for ln in output.splitlines() if XRUN_ERROR.search(ln)]
        if errors or SNAPSHOT_WRITTEN not in output:
            why = "\n".join(errors[:20]) or f"no {SNAPSHOT_WRITTEN!r} line"
            raise ConfigurationError(f"Xcelium elaboration failed (see {self.build_log}):\n{why}")

    def finish(self) -> None:
        """Write the run script (last: its presence means "built")."""
        tmp = self.exe.with_suffix(".tmp")
        # xrun requires the -cds_implicit_tmpdir directory to exist.
        tmp.write_text(
            f'#!/bin/sh\nmkdir -p .cds_tmp\nexec {shlex.join(self.run_command())} "$@"\n'
        )
        tmp.chmod(0o755)
        tmp.rename(self.exe)


class XceliumPlatform(SimulatorPlatform):
    """Run the device variant's testbench under Xcelium.

    Options:
        ``--xcelium-workdir``: where snapshots are built (shared across runs).
        ``--xrun``: the xrun executable.
        ``--xcelium-args``: extra xrun arguments for elaboration.
        ``--xcelium-build-prefix``: a command to elaborate under, e.g. ``srun``.
        ``--waves``: record waveforms to :data:`WAVES_FILE` in the run directory.
    """

    name: ClassVar[str] = "xcelium"
    WAVES_FILE: ClassVar[str] = "sim.trn"  # open this in SimVision

    @classmethod
    def add_options(cls, parser: pytest.Parser) -> None:  # noqa: D102
        group = parser.getgroup("soc-pastel xcelium")
        group.addoption("--xcelium-workdir", help="Snapshot build directory.")
        group.addoption("--xrun", default="xrun", help="xrun executable.")
        group.addoption(
            "--xcelium-args",
            metavar="ARGS",
            help="Extra xrun arguments for elaboration, as one shell-quoted string. Part of "
            "the snapshot's identity, so changing them builds a new snapshot.",
        )
        group.addoption(
            "--xcelium-build-prefix",
            metavar="CMD",
            help="Command to elaborate under, e.g. 'srun -c 8'. Default: on this machine.",
        )

    def make_image(self, sources: SimSources) -> XceliumImage:  # noqa: D102
        return XceliumImage(
            sources,
            self.workdir,
            self.options.get("xrun") or "xrun",
            build_prefix=self.option_args("xcelium_build_prefix"),
            waves=self.waves,
            extra_args=self.option_args("xcelium_args"),
        )
