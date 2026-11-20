"""The VCS simulation platform (``--platform=vcs``). Experimental: untested.

Written from VCS's documented command-line flow, but nobody has run it yet.
If you have VCS, please try it and report what needed changing.

An image is ``simv`` (with its ``simv.daidir``), built by one ``vcs`` call
that also compiles PASTEL's DPI C++. Waves (``--waves``) are a VCD written by
PASTEL's ``pastel_server``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from .logcheck import COMMON_RUN_RULES, COMPILER_RULES, LogRules
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

#: A VCS error message, e.g. ``Error-[SE] Syntax error``.
VCS_ERROR = re.compile(r"^Error-\[")
#: A VCS warning or lint message, e.g. ``Warning-[TFIPC] ...``.
VCS_WARNING = re.compile(r"^(Warning|Lint)-\[")


class VcsImage(SimulatorImage):
    """A VCS ``simv`` of a device variant's testbench."""

    tool: ClassVar[str] = "vcs"
    TOOL_ENV: ClassVar[tuple[str, ...]] = (*SimulatorImage.TOOL_ENV, "VCS_HOME")
    BUILD_RULES: ClassVar[LogRules] = COMPILER_RULES.merged(
        LogRules(errors=(VCS_ERROR.pattern,), warnings=(VCS_WARNING.pattern,))
    )

    def __init__(
        self,
        sources: SimSources,
        workdir: Path,
        vcs: str,
        *,
        waves: bool = False,
        extra_args: Sequence[str] = (),
    ) -> None:
        super().__init__(sources, workdir, vcs, waves=waves, extra_args=extra_args)

    @property
    def exe(self) -> Path:  # noqa: D102
        return self.dir / "simv"

    def command(self) -> list[str]:
        """The vcs command line that builds ``simv``."""
        return [
            self.executable,
            "-full64", "-sverilog",
            "-timescale=1ns/1ps",
            "-top", self.sources.top,
            "-Mdir=" + str(self.dir / "csrc"),
            "-o", str(self.exe),
            "-l", str(self.dir / "vcs.log"),
            *(["-debug_access+r", "+define+PASTEL_WAVES"] if self.waves else []),
            *self.extra_args,
            f"+incdir+{SIM_HW}",
            *map(str, PASTEL_HDL),
            *standard_source_args(self.sources.args),
            *map(str, PASTEL_CPP),
            "-CFLAGS", f"-I{SIM_CSRC} -std=c++17",
        ]  # fmt: skip

    def steps(self) -> list[list[str]]:  # noqa: D102
        return [self.command()]


class VcsPlatform(SimulatorPlatform):
    """Run the device variant's testbench under VCS. Experimental: untested.

    Options:
        ``--vcs-workdir``: where models are built (shared across runs).
        ``--vcs``: the vcs executable.
        ``--vcs-args``: extra vcs arguments.
        ``--waves``: dump a VCD to :data:`WAVES_FILE` in the run directory.
    """

    name: ClassVar[str] = "vcs"
    WAVES_FILE: ClassVar[str] = "sim.vcd"
    #: $error/$fatal print ``Error: "file", line: ...``; VCS's own ``Error-[...]``.
    RUN_RULES: ClassVar[LogRules] = COMMON_RUN_RULES.merged(
        LogRules(
            errors=(VCS_ERROR.pattern, r'^(Error|Fatal): "'),
            warnings=(VCS_WARNING.pattern, r'^Warning: "'),
        )
    )

    @classmethod
    def add_options(cls, parser: pytest.Parser) -> None:  # noqa: D102
        group = parser.getgroup("soc-pastel vcs (experimental)")
        group.addoption("--vcs-workdir", help="Model build directory.")
        group.addoption("--vcs", default="vcs", help="vcs executable.")
        group.addoption(
            "--vcs-args",
            metavar="ARGS",
            help="Extra vcs arguments, as one shell-quoted string. Part of the model's "
            "identity, so changing them builds a new model.",
        )

    def make_image(self, sources: SimSources) -> VcsImage:  # noqa: D102
        return VcsImage(
            sources,
            self.workdir,
            self.options.get("vcs") or "vcs",
            waves=self.waves,
            extra_args=self.option_args("vcs_args"),
        )
