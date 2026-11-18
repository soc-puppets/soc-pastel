"""The Verilator simulation platform."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from .logcheck import COMMON_RUN_RULES, COMPILER_RULES, LogRules
from .simulator import (  # noqa: F401 - re-exported; device packs import these from here
    GPIO_HW,
    OCD_HW,
    PASTEL_CPP,
    PASTEL_HDL,
    SIM_CSRC,
    SIM_HW,
    SimulatorImage,
    SimulatorPlatform,
    _content_key,
    _headers,
)
from .sources import SimSources


class VerilatorImage(SimulatorImage):
    """A Verilator model of a device variant's testbench.

    The testbench top is plain SystemVerilog with no ports, as for any other
    simulator: Verilator supplies the ``main`` and the scheduler (``--binary``).
    """

    tool: ClassVar[str] = "verilator"
    TOOL_ENV: ClassVar[tuple[str, ...]] = (*SimulatorImage.TOOL_ENV, "VERILATOR_ROOT")
    BUILD_RULES: ClassVar[LogRules] = COMPILER_RULES.merged(
        LogRules(errors=(r"%Error",), warnings=(r"%Warning",))
    )

    def __init__(
        self,
        sources: SimSources,
        workdir: Path,
        verilator: str,
        jobs: int,
        waves: bool = False,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.jobs = jobs
        super().__init__(sources, workdir, verilator, waves=waves, extra_args=extra_args)

    @property
    def verilator(self) -> str:
        """The verilator executable."""
        return self.executable

    @property
    def exe(self) -> Path:  # noqa: D102
        return self.dir / "Vsim"

    def command(self) -> list[str]:
        """The Verilator command line that builds the model."""
        return [
            self.verilator,
            "--binary",
            "-j",
            str(self.jobs),
            "--top-module",
            self.sources.top,
            "--Mdir",
            str(self.dir / "obj"),
            "-o",
            str(self.exe),
            "-Wno-fatal",
            "-Wno-lint",
            "-Wno-style",
            "-Wno-TIMESCALEMOD",
            *(["--trace-fst", "+define+PASTEL_WAVES"] if self.waves else []),
            *self.extra_args,
            "-CFLAGS",
            f"-I{SIM_CSRC}",
            f"+incdir+{SIM_HW}",
            *map(str, PASTEL_HDL),
            *self.sources.args,
            *map(str, PASTEL_CPP),
        ]

    def steps(self) -> list[list[str]]:  # noqa: D102
        return [self.command()]


class VerilatorPlatform(SimulatorPlatform):
    """Run the device variant's testbench under Verilator.

    Options:
        ``--verilator-workdir``: where models are built (shared across runs).
        ``--verilator``: the verilator executable.
        ``--verilator-args``: extra Verilator arguments (e.g. compiler flags).
        ``--waves``: dump waveforms to :data:`WAVES_FILE` in the run directory.
    """

    name: ClassVar[str] = "verilator"
    WAVES_FILE: ClassVar[str] = "sim.fst"
    RUN_RULES: ClassVar[LogRules] = COMMON_RUN_RULES.merged(
        LogRules(errors=(r"%(Error|Fatal)",), warnings=(r"%Warning",))
    )
    #: Verilator stops at the first $error (+verilator+error+limit), and says so.
    STOP_MESSAGES: ClassVar[tuple[str, ...]] = (r"%Error: .*: Verilog \$stop",)

    @classmethod
    def add_options(cls, parser: pytest.Parser) -> None:  # noqa: D102
        group = parser.getgroup("soc-pastel verilator")
        group.addoption("--verilator-workdir", help="Model build directory.")
        group.addoption("--verilator", default="verilator", help="Verilator executable.")
        group.addoption(
            "--verilator-args",
            metavar="ARGS",
            help="Extra Verilator arguments, as one shell-quoted string, e.g. "
            '"-CFLAGS -fcoroutines -LDFLAGS -Wl,-rpath,/opt/gcc/lib64". Part of the model\'s '
            "identity, so changing them builds a new model.",
        )

    def make_image(self, sources: SimSources) -> VerilatorImage:  # noqa: D102
        return VerilatorImage(
            sources,
            self.workdir,
            self.options.get("verilator") or "verilator",
            os.cpu_count() or 4,
            waves=self.waves,
            extra_args=self.option_args("verilator_args"),
        )
