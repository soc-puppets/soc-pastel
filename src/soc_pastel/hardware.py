"""Hardware platforms: a device on a real board.

``--platform=fpga`` runs tests on an FPGA prototype that is already powered
and connected through a debug probe. If ``--fpga-program=<command>`` is given,
the command (e.g. one that loads the bitstream) runs once per pytest session,
before the first test, with its output in that test's run directory as
``program.log``.

A hardware platform has no backdoor, simulation time or waves, so tests that
need them are skipped. Firmware loads, and RTT is read, through on-chip debug.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import ClassVar

import pytest

from soc_pastel.core.errors import ConfigurationError
from soc_pastel.core.platform import HardwarePlatform, PlatformKind

log = logging.getLogger(__name__)


class FpgaPlatform(HardwarePlatform):
    """An FPGA prototype on the bench, reached through its debug probe.

    Options:
        ``--fpga-program``: a shell command that programs the FPGA, run once
        per session before the first test.
    """

    name: ClassVar[str] = "fpga"
    kind: ClassVar[PlatformKind] = PlatformKind.FPGA

    #: Programming commands already run in this process.
    _programmed: ClassVar[set[str]] = set()

    _running = False

    @classmethod
    def add_options(cls, parser: pytest.Parser) -> None:  # noqa: D102
        group = parser.getgroup("soc-pastel fpga")
        group.addoption(
            "--fpga-program",
            metavar="CMD",
            help="Shell command that programs the FPGA, run once before the first test.",
        )

    def prepare(self) -> None:
        """Program the FPGA with ``--fpga-program``, once per session."""
        command = self.options.get("fpga_program")
        if not command or command in self._programmed:
            return
        out = self.run_dir / "program.log"
        log.info("programming the FPGA: %s", command)
        began = time.monotonic()
        with open(out, "w") as f:
            result = subprocess.run(command, shell=True, stdout=f, stderr=subprocess.STDOUT)
        if result.returncode != 0:
            tail = out.read_text(errors="replace")[-2000:]
            raise ConfigurationError(
                f"programming the FPGA failed (exit status {result.returncode}; see {out}):\n{tail}"
            )
        self._programmed.add(command)
        log.info("programmed the FPGA in %.0f s", time.monotonic() - began)

    def start(self) -> None:  # noqa: D102
        self.prepare()
        self._running = True

    def stop(self) -> None:  # noqa: D102
        self._running = False

    @property
    def running(self) -> bool:  # noqa: D102
        return self._running
