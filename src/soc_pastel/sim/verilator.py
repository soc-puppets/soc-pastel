"""The Verilator simulation platform."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest

from soc_pastel import HDL_ROOT
from soc_pastel.core.capabilities import WAVES
from soc_pastel.core.device import Device, Variant
from soc_pastel.core.endpoints import EndpointHandler
from soc_pastel.core.errors import ConfigurationError, TargetError
from soc_pastel.core.memory import MemoryInterface
from soc_pastel.core.platform import PlatformImage, SimulationPlatform
from soc_pastel.core.util import file_lock

from .handlers import Backdoor, BackdoorMemory, PlatformControl
from .session import Listener, SocketEndpointSession
from .sources import SimSources, load_sim_sources

SIM_HW = HDL_ROOT / "sim" / "hw"
SIM_CSRC = HDL_ROOT / "sim" / "csrc"
OCD_HW = HDL_ROOT / "interfaces" / "on_chip_debug" / "hw"

#: PASTEL's own HDL and DPI C++, compiled into every Verilator model.
PASTEL_HDL = [
    SIM_HW / "pastel_ep_pkg.sv",
    SIM_HW / "pastel_clock.sv",
    SIM_HW / "pastel_server.sv",
    OCD_HW / "pastel_jtag.sv",
]
PASTEL_CPP = [SIM_CSRC / "pastel_ep.cpp", OCD_HW / "pastel_jtag.cpp"]


def _content_key(path: Path) -> bytes:
    """File content for build caching, ignoring comment-only lines.

    Generators often stamp the date into a comment (VeeR's configuration
    generator does), which would otherwise force a rebuild every run.
    """
    lines = path.read_bytes().splitlines()
    return b"\n".join(ln for ln in lines if not ln.lstrip().startswith((b"//", b"#")))


class VerilatorImage(PlatformImage):
    """A Verilator model of a device variant's testbench.

    The testbench top is plain SystemVerilog with no ports, as for any other
    simulator: Verilator supplies the ``main`` and the scheduler (``--binary``).

    A model that can dump waveforms is slower, so it is a separate image
    (its key ends in ``-waves``) built only when waves are asked for.
    """

    def __init__(
        self, sources: SimSources, workdir: Path, verilator: str, jobs: int, waves: bool = False
    ) -> None:
        self.sources = sources
        self.verilator = verilator
        self.jobs = jobs
        self.waves = waves
        h = hashlib.sha256(sources.key.encode() + sources.top.encode())
        for arg in [*map(str, PASTEL_HDL + PASTEL_CPP), *sources.args]:
            h.update(arg.encode())
            p = Path(arg)
            if not arg.startswith(("+", "-")) and p.is_file():
                h.update(_content_key(p))
        self._key = f"{sources.top}-{h.hexdigest()[:12]}" + ("-waves" if waves else "")
        self.dir = workdir / "images" / self._key
        self.exe = self.dir / "Vsim"

    @property
    def key(self) -> str:  # noqa: D102
        return self._key

    def is_built(self) -> bool:  # noqa: D102
        return self.exe.is_file()

    def build(self) -> None:  # noqa: D102
        with file_lock(self.dir.with_suffix(".lock")):
            if self.is_built():
                return
            if shutil.which(self.verilator) is None:
                raise ConfigurationError(f"{self.verilator} not found")
            self.dir.mkdir(parents=True, exist_ok=True)
            cmd = [
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
                "-CFLAGS",
                f"-I{SIM_CSRC}",
                f"+incdir+{SIM_HW}",
                *map(str, PASTEL_HDL),
                *self.sources.args,
                *map(str, PASTEL_CPP),
            ]
            log = self.dir / "build.log"
            with open(log, "w") as f:
                f.write(" ".join(cmd) + "\n\n")
                f.flush()
                result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
            if result.returncode != 0 or not self.is_built():
                tail = log.read_text()[-3000:]
                raise ConfigurationError(f"Verilator build failed (see {log}):\n{tail}")


class VerilatorPlatform(SimulationPlatform):
    """Run the device variant's testbench under Verilator.

    Options:
        ``--verilator-workdir``: where models are built (shared across runs).
        ``--verilator``: the verilator executable.
        ``--waves``: dump waveforms to :data:`WAVES_FILE` in the run directory.
    """

    #: Waveform file written to the run directory when waves are enabled.
    WAVES_FILE: ClassVar[str] = "sim.fst"

    name: ClassVar[str] = "verilator"
    platform_handlers: ClassVar[Mapping[str, type[EndpointHandler]]] = {
        PlatformControl.type_name: PlatformControl,
        Backdoor.type_name: Backdoor,
    }

    def __init__(self, device: Device, variant: Variant, options: Mapping[str, Any]) -> None:
        super().__init__(device, variant, options)
        self.workdir = Path(options.get("verilator_workdir") or ".pastel/verilator").resolve()
        self.waves = bool(options.get("waves"))
        self._image: VerilatorImage | None = None
        self._proc: subprocess.Popen | None = None
        self._session: SocketEndpointSession | None = None
        self._listener: Listener | None = None
        self._log = None

    @classmethod
    def add_options(cls, parser: pytest.Parser) -> None:  # noqa: D102
        group = parser.getgroup("soc-pastel verilator")
        group.addoption("--verilator-workdir", help="Model build directory.")
        group.addoption("--verilator", default="verilator", help="Verilator executable.")

    def capabilities(self) -> frozenset[str]:
        """Backdoor and simulation time, plus waves when run with ``--waves``."""
        features = super().capabilities()
        return features | {WAVES} if self.waves else features

    def image(self) -> VerilatorImage:  # noqa: D102
        if self._image is None:
            sources = load_sim_sources(self.variant, self.workdir / "sources")
            self._image = VerilatorImage(
                sources,
                self.workdir,
                self.options.get("verilator") or "verilator",
                os.cpu_count() or 4,
                waves=self.waves,
            )
        return self._image

    @property
    def endpoints(self) -> SocketEndpointSession:  # noqa: D102
        if self._session is None:
            raise TargetError("simulation is not running")
        return self._session

    def start(self) -> None:  # noqa: D102
        image = self.image()
        image.build()
        self._listener = Listener()
        self._log = open(self.run_dir / "sim.log", "w")
        args = [str(image.exe), f"+pastel_host={self._listener.spec}"]
        if self.waves:
            args.append(f"+pastel_waves={self.WAVES_FILE}")  # relative to cwd, the run dir
        self._proc = subprocess.Popen(
            args,
            cwd=self.run_dir,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        conn = self._listener.accept(120.0, alive=lambda: self._proc.poll() is None)
        self._session = SocketEndpointSession(
            conn, self._listener.token, self.expected_endpoints, self.platform_handlers
        )

    def stop(self) -> None:  # noqa: D102
        if self._session is not None:
            try:
                self._control().request_finish()
            except Exception:
                pass
            self._session.close()
            self._session = None
        if self._proc is not None:
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._log is not None:
            self._log.close()
            self._log = None

    @property
    def running(self) -> bool:  # noqa: D102
        return (
            self._proc is not None
            and self._proc.poll() is None
            and self._session is not None
            and not self._session.closed
        )

    def _control(self) -> PlatformControl:
        return self.endpoints.handler("platform")  # type: ignore[return-value]

    def time_ns(self) -> int | None:  # noqa: D102
        return self._control().time_ns()

    def set_waves(self, enable: bool) -> bool:  # noqa: D102
        return self._control().set_waves(enable) if self.waves else False

    def backdoor(self) -> MemoryInterface | None:  # noqa: D102
        session = self.endpoints
        doors = {
            e.name: session.handler(e.name)
            for e in session.hello.endpoints
            if e.type == Backdoor.type_name
        }
        return BackdoorMemory(self.variant, doors)  # type: ignore[arg-type]

    def debug_connection(self) -> Mapping[str, Any]:  # noqa: D102
        port = self.endpoints.hello.info.get("jtag_port")
        return {"remote_bitbang": ("127.0.0.1", int(port))} if port else {}
