"""The Verilator simulation platform."""

from __future__ import annotations

import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest

from soc_pastel import HDL_ROOT
from soc_pastel.core.capabilities import WAVES
from soc_pastel.core.device import Device, Variant
from soc_pastel.core.endpoints import EndpointHandler
from soc_pastel.core.errors import ConfigurationError, PastelTimeout, ProtocolError, TargetError
from soc_pastel.core.memory import MemoryInterface
from soc_pastel.core.platform import PlatformImage, SimulationPlatform
from soc_pastel.core.util import file_lock

from .handlers import Backdoor, BackdoorMemory, Pipe, PlatformControl
from .launch import SimLauncher, SimProcess, connect_timeout, make_launcher
from .session import Listener, SocketEndpointSession, accept_session
from .sources import SimSources, load_sim_sources

log = logging.getLogger(__name__)

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

    The key covers the sources (by content), PASTEL's own HDL and C++, the
    extra Verilator arguments, which ``verilator`` runs (by resolved path)
    and the compiler environment (:data:`TOOL_ENV`). It can't see a
    different Verilator installed at the same path; use another
    ``--verilator-workdir`` after an in-place upgrade.
    """

    #: Environment variables that change how the model is compiled.
    TOOL_ENV = ("CXX", "CC", "CXXFLAGS", "CFLAGS", "CPPFLAGS", "LDFLAGS", "VERILATOR_ROOT")
    #: How long to wait for the model to appear after a successful build, for
    #: builds run on another machine whose output reaches us over NFS.
    APPEAR_TIMEOUT_S = 30.0

    def __init__(
        self,
        sources: SimSources,
        workdir: Path,
        verilator: str,
        jobs: int,
        waves: bool = False,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self.sources = sources
        self.verilator = verilator
        self.jobs = jobs
        self.waves = waves
        self.extra_args = (*sources.tool_args.get("verilator", ()), *extra_args)
        h = hashlib.sha256(sources.key.encode() + sources.top.encode())
        for arg in [*map(str, PASTEL_HDL + PASTEL_CPP), *sources.args]:
            h.update(arg.encode())
            p = Path(arg)
            if not arg.startswith(("+", "-")) and p.is_file():
                h.update(_content_key(p))
        tool = [
            "args",
            *self.extra_args,
            "verilator",
            shutil.which(verilator) or verilator,
            *(f"{v}={os.environ.get(v, '')}" for v in self.TOOL_ENV),
        ]
        h.update("\0".join(tool).encode())
        self._key = f"{sources.top}-{h.hexdigest()[:12]}" + ("-waves" if waves else "")
        self.dir = workdir / "images" / self._key
        self.exe = self.dir / "Vsim"

    @property
    def key(self) -> str:  # noqa: D102
        return self._key

    def is_built(self) -> bool:  # noqa: D102
        return self.exe.is_file()

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

    def build(self) -> None:  # noqa: D102
        with file_lock(self.dir.with_suffix(".lock")):
            if self.is_built():
                return
            if shutil.which(self.verilator) is None:
                raise ConfigurationError(f"{self.verilator} not found")
            self.dir.mkdir(parents=True, exist_ok=True)
            cmd = self.command()
            build_log = self.dir / "build.log"
            with open(build_log, "w") as f:
                f.write(shlex.join(cmd) + "\n\n")
                f.flush()
                result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
            tail = build_log.read_text(errors="replace")[-3000:]
            if result.returncode != 0:
                raise ConfigurationError(f"Verilator build failed (see {build_log}):\n{tail}")
            if not self._appeared():
                raise ConfigurationError(
                    f"Verilator exited successfully but {self.exe} does not exist "
                    f"after {self.APPEAR_TIMEOUT_S:.0f} s. If it was built on another "
                    f"machine, this one's file system hasn't seen it yet (see {build_log}):"
                    f"\n{tail}"
                )

    def _appeared(self) -> bool:
        """Wait for the model, re-listing its directory so NFS revalidates it."""
        deadline = time.monotonic() + self.APPEAR_TIMEOUT_S
        while True:
            try:
                os.listdir(self.dir)
            except OSError:
                pass
            if self.is_built():
                return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.5)


class VerilatorPlatform(SimulationPlatform):
    """Run the device variant's testbench under Verilator.

    Options:
        ``--verilator-workdir``: where models are built (shared across runs).
        ``--verilator``: the verilator executable.
        ``--verilator-args``: extra Verilator arguments (e.g. compiler flags).
        ``--waves``: dump waveforms to :data:`WAVES_FILE` in the run directory.

    The model runs through the launcher chosen with ``--sim-launcher`` or
    ``--sim-launcher-class`` (see :mod:`soc_pastel.sim.launch`), locally by
    default. A remote launcher needs the build directory and the run
    directory on a file system the simulation machine shares.
    """

    #: Waveform file written to the run directory when waves are enabled.
    WAVES_FILE: ClassVar[str] = "sim.fst"

    name: ClassVar[str] = "verilator"
    platform_handlers: ClassVar[Mapping[str, type[EndpointHandler]]] = {
        PlatformControl.type_name: PlatformControl,
        Backdoor.type_name: Backdoor,
        Pipe.type_name: Pipe,
    }

    #: Sidecar list written to the run directory for ``+pastel_sidecars``.
    SIDECARS_FILE: ClassVar[str] = "sidecars.tsv"

    def __init__(self, device: Device, variant: Variant, options: Mapping[str, Any]) -> None:
        super().__init__(device, variant, options)
        self.workdir = Path(options.get("verilator_workdir") or ".pastel/verilator").resolve()
        self.waves = bool(options.get("waves"))
        self._image: VerilatorImage | None = None
        self._launcher: SimLauncher = make_launcher(options)
        self._proc: SimProcess | None = None
        self._session: SocketEndpointSession | None = None
        self._listener: Listener | None = None
        self._log = None

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

    @property
    def waves_path(self) -> Path | None:  # noqa: D102
        return self.run_dir / self.WAVES_FILE if self.waves else None

    def capabilities(self) -> frozenset[str]:
        """Backdoor and simulation time, plus waves when run with ``--waves``."""
        features = super().capabilities()
        return features | {WAVES} if self.waves else features

    def image(self) -> VerilatorImage:  # noqa: D102
        if self._image is None:
            sources = load_sim_sources(self.variant, self.workdir / "sources", self.name)
            self._image = VerilatorImage(
                sources,
                self.workdir,
                self.options.get("verilator") or "verilator",
                os.cpu_count() or 4,
                waves=self.waves,
                extra_args=tuple(shlex.split(self.options.get("verilator_args") or "")),
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
        remote = self._launcher.remote
        self._listener = Listener(
            bind="" if remote else "127.0.0.1",
            advertise=self.options.get("sim_host_address") if remote else None,
        )
        run_dir = self.run_dir.resolve()
        log_path = run_dir / "sim.log"
        self._log = open(log_path, "wb")
        args = [str(image.exe.resolve()), f"+pastel_host={self._listener.spec}"]
        if self.sidecars:
            (run_dir / self.SIDECARS_FILE).write_text(
                "".join("\t".join([sc.name, sc.log, *sc.argv]) + "\n" for sc in self.sidecars)
            )
            args.append(f"+pastel_sidecars={self.SIDECARS_FILE}")  # relative to the run dir
        if self.waves:
            args.append(f"+pastel_waves={self.WAVES_FILE}")  # relative to cwd, the run dir
        self._proc = self._launcher.launch(args, run_dir, self._log)
        try:
            self._session = accept_session(
                self._listener,
                connect_timeout(self.options, self._launcher),
                self.expected_endpoints,
                self.platform_handlers,
                alive=lambda: self._proc.poll() is None,
            )
        except (ProtocolError, PastelTimeout) as e:
            self._log.flush()
            tail = log_path.read_text(errors="replace")[-2000:].strip()
            raise type(e)(f"{e}; see {log_path}" + (f":\n{tail}" if tail else "")) from None

    def stop(self) -> None:  # noqa: D102
        if self._session is not None:
            try:
                self._control().request_finish()
            except Exception:
                pass
            self._session.close()
            self._session = None
        if self._proc is not None:
            # A remote simulator also exits by itself once its connection
            # closes; terminating the launcher (srun, ssh) is the fallback.
            for escalate in (self._proc.terminate, self._proc.kill):
                try:
                    self._proc.wait(timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    escalate()
            else:
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
