"""The Questa (ModelSim) simulation platform (``--platform=questa``). Experimental: untested.

Written from Questa's documented command-line flow, but nobody has run it
yet. If you have Questa, please try it and report what needed changing.

An image is a compiled and optimised design in a ``work`` library, PASTEL's
DPI library and a ``run`` script that calls ``vsim -c`` on them. Waves
(``--waves``) are a VCD written by PASTEL's ``pastel_server``.

``vlib``, ``vlog`` and ``vopt`` are taken from the directory ``--vsim``
resolves to, so pointing ``--vsim`` at one installation uses all of it.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from soc_pastel.core.errors import ConfigurationError

from .simulator import (
    PASTEL_CPP,
    PASTEL_HDL,
    SIM_CSRC,
    SIM_HW,
    SimulatorImage,
    SimulatorPlatform,
    standard_source_args,
)
from .sources import SimSources

#: A Questa error or fatal message, e.g. ``** Error: tb.sv(3): ...``.
QUESTA_ERROR = re.compile(r"^\*\* (Error|Fatal)", re.M)


def questa_include_dir(vsim: str) -> Path:
    """The directory holding Questa's ``svdpi.h``: ``include`` beside its ``bin``."""
    candidates = []
    for var in ("QUESTA_HOME", "MTI_HOME"):
        if os.environ.get(var):
            candidates.append(Path(os.environ[var]) / "include")
    exe = shutil.which(vsim)
    if exe:
        candidates.append(Path(exe).resolve().parent.parent / "include")
    for inc in candidates:
        if (inc / "svdpi.h").is_file():
            return inc
    raise ConfigurationError(
        "cannot find Questa's svdpi.h: set QUESTA_HOME (or MTI_HOME), or put vsim on PATH"
    )


class QuestaImage(SimulatorImage):
    """A Questa ``work`` library with the optimised design, the DPI library and a run script."""

    tool: ClassVar[str] = "questa"
    TOOL_ENV: ClassVar[tuple[str, ...]] = (*SimulatorImage.TOOL_ENV, "QUESTA_HOME", "MTI_HOME")
    OPTIMISED: ClassVar[str] = "pastel_opt"

    def __init__(
        self,
        sources: SimSources,
        workdir: Path,
        vsim: str,
        *,
        waves: bool = False,
        extra_args: Sequence[str] = (),
    ) -> None:
        super().__init__(sources, workdir, vsim, waves=waves, extra_args=extra_args)
        self.work = self.dir / "work"
        self.dpi = self.dir / "libpastel_dpi.so"

    @property
    def exe(self) -> Path:  # noqa: D102
        return self.dir / "run"

    def tool_path(self, name: str) -> str:
        """``vlog``, ``vopt``, ... from the same installation as ``vsim``."""
        exe = shutil.which(self.executable)
        if exe:
            sibling = Path(exe).resolve().parent / name
            if sibling.is_file():
                return str(sibling)
        return name

    def steps(self) -> list[list[str]]:  # noqa: D102
        return [
            [self.tool_path("vlib"), str(self.work)],
            [
                self.tool_path("vlog"), "-sv", "-work", str(self.work),
                "-timescale", "1ns/1ps",
                *(["+define+PASTEL_WAVES"] if self.waves else []),
                *self.extra_args,
                f"+incdir+{SIM_HW}",
                *map(str, PASTEL_HDL),
                *standard_source_args(self.sources.args),
            ],
            [
                self.tool_path("vopt"), "-work", str(self.work),
                *(["+acc"] if self.waves else []),
                self.sources.top, "-o", self.OPTIMISED,
            ],
            [
                os.environ.get("CXX", "g++"), "-shared", "-fPIC", "-O2", "-std=c++17",
                f"-I{SIM_CSRC}", f"-I{questa_include_dir(self.executable)}",
                *map(str, PASTEL_CPP),
                "-o", str(self.dpi),
            ],
        ]  # fmt: skip

    def run_command(self) -> list[str]:
        """What the run script executes; plusargs are appended."""
        return [
            self.executable, "-c",
            "-lib", str(self.work), self.OPTIMISED,
            "-sv_lib", str(self.dpi.with_suffix("")),  # vsim adds the .so
            "-onfinish", "exit",
            "-l", "vsim.log",
            "-do", "run -all; quit -f",
        ]  # fmt: skip

    def check_step(self, index: int, output: str) -> None:
        """Any ``** Error`` or ``** Fatal`` fails the build, whatever the exit status."""
        errors = [ln for ln in output.splitlines() if QUESTA_ERROR.match(ln)]
        if errors:
            raise ConfigurationError(
                f"Questa build failed (see {self.build_log}):\n" + "\n".join(errors[:20])
            )

    def finish(self) -> None:
        """Write the run script (last: its presence means "built")."""
        tmp = self.exe.with_suffix(".tmp")
        tmp.write_text(f'#!/bin/sh\nexec {shlex.join(self.run_command())} "$@"\n')
        tmp.chmod(0o755)
        tmp.rename(self.exe)


class QuestaPlatform(SimulatorPlatform):
    """Run the device variant's testbench under Questa. Experimental: untested.

    Options:
        ``--questa-workdir``: where models are built (shared across runs).
        ``--vsim``: the vsim executable; vlib, vlog and vopt come from beside it.
        ``--questa-args``: extra vlog arguments.
        ``--waves``: dump a VCD to :data:`WAVES_FILE` in the run directory.
    """

    name: ClassVar[str] = "questa"
    WAVES_FILE: ClassVar[str] = "sim.vcd"

    @classmethod
    def add_options(cls, parser: pytest.Parser) -> None:  # noqa: D102
        group = parser.getgroup("soc-pastel questa (experimental)")
        group.addoption("--questa-workdir", help="Model build directory.")
        group.addoption("--vsim", default="vsim", help="vsim executable.")
        group.addoption(
            "--questa-args",
            metavar="ARGS",
            help="Extra vlog arguments, as one shell-quoted string. Part of the model's "
            "identity, so changing them builds a new model.",
        )

    def make_image(self, sources: SimSources) -> QuestaImage:  # noqa: D102
        return QuestaImage(
            sources,
            self.workdir,
            self.options.get("vsim") or "vsim",
            waves=self.waves,
            extra_args=self.option_args("questa_args"),
        )
