"""What every simulation platform shares, whichever simulator runs the model.

A simulator platform is two classes:

* a :class:`SimulatorImage`: how to build the model (its build steps and the
  executable that runs it), and what makes two models interchangeable (its
  key); and
* a :class:`SimulatorPlatform`: its options and which image to build. Launching
  the model, connecting to it, sidecars, waves, simulation time, the backdoor
  and stopping are all here.

See :mod:`soc_pastel.sim.verilator` for the smallest example.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import time
from abc import abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

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
from .logcheck import (
    COMMAND_ECHO,
    COMMON_RUN_RULES,
    COMPILER_RULES,
    EXIT_STATUS_ERROR,
    LogFindings,
    LogRules,
)
from .session import Listener, SocketEndpointSession, accept_session
from .sources import SimSources, load_sim_sources

log = logging.getLogger(__name__)

SIM_HW = HDL_ROOT / "sim" / "hw"
SIM_CSRC = HDL_ROOT / "sim" / "csrc"
OCD_HW = HDL_ROOT / "interfaces" / "on_chip_debug" / "hw"
GPIO_HW = HDL_ROOT / "interfaces" / "gpio" / "hw"

#: PASTEL's own HDL, compiled into every model.
PASTEL_HDL = [
    SIM_HW / "pastel_ep_pkg.sv",
    SIM_HW / "pastel_clock.sv",
    SIM_HW / "pastel_server.sv",
    OCD_HW / "pastel_jtag.sv",
    GPIO_HW / "pastel_gpio.sv",
]
#: PASTEL's DPI C++, compiled into every model.
PASTEL_CPP = [SIM_CSRC / "pastel_ep.cpp", OCD_HW / "pastel_jtag.cpp"]
#: Directories PASTEL's own HDL and C++ include from.
PASTEL_INCDIRS = [SIM_HW, SIM_CSRC, OCD_HW]


def _content_key(path: Path) -> bytes:
    """File content for build caching, ignoring comment-only lines.

    Generators often stamp the date into a comment (VeeR's configuration
    generator does), which would otherwise force a rebuild every run.
    """
    lines = path.read_bytes().splitlines()
    return b"\n".join(ln for ln in lines if not ln.lstrip().startswith((b"//", b"#")))


def standard_source_args(args: Sequence[str]) -> list[str]:
    """Simulator-independent source args, for simulators other than Verilator.

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


#: Files in include directories that can change a model without being listed.
HEADER_SUFFIXES = frozenset({".svh", ".vh", ".h", ".hpp", ".hh", ".inc"})


def _headers(dirs: list[Path]) -> list[Path]:
    """Header files directly inside each include directory, in a stable order."""
    found: list[Path] = []
    for d in dict.fromkeys(dirs):  # unique, in order
        if d.is_dir():
            found += sorted(f for f in d.iterdir() if f.suffix in HEADER_SUFFIXES and f.is_file())
    return found


class SimulatorImage(PlatformImage):
    """A model of a device variant's testbench, built by one simulator.

    The key covers the sources (by content), PASTEL's own HDL and C++, the
    headers in every include directory (``+incdir+``, by content), the
    extra arguments for this simulator, which tool runs (by resolved path)
    and the compiler environment (:attr:`TOOL_ENV`). It can't see a
    different version installed at the same path; use another work directory
    after an in-place upgrade.

    A model that can dump waveforms is a separate image (its key ends in
    ``-waves``), built only when waves are asked for.

    Subclasses set :attr:`tool` and provide :attr:`exe` and :meth:`steps`.

    Args:
        sources: The testbench to build.
        workdir: The platform's work directory; images go in ``images/``.
        executable: The simulator's main tool (``verilator``, ``xrun``, ...).
        waves: Build a model that can dump waveforms.
        extra_args: Extra arguments for the simulator, after those in
            ``sources.tool_args[tool]``.
    """

    #: The platform's name: the key of :attr:`SimSources.tool_args` it reads.
    tool: ClassVar[str]
    #: Environment variables that change how the model is compiled.
    TOOL_ENV: ClassVar[tuple[str, ...]] = (
        "CXX",
        "CC",
        "CXXFLAGS",
        "CFLAGS",
        "CPPFLAGS",
        "LDFLAGS",
    )
    #: How long to wait for the model to appear after a successful build, for
    #: builds run on another machine whose output reaches us over NFS.
    APPEAR_TIMEOUT_S: ClassVar[float] = 30.0
    #: What counts as an error or a warning in the build log (see
    #: :mod:`soc_pastel.sim.logcheck`). Subclasses add their simulator's.
    BUILD_RULES: ClassVar[LogRules] = COMPILER_RULES

    def __init__(
        self,
        sources: SimSources,
        workdir: Path,
        executable: str,
        *,
        waves: bool = False,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.sources = sources
        self.executable = executable
        self.waves = waves
        self.extra_args = (*sources.tool_args.get(self.tool, ()), *extra_args)
        self._key = f"{sources.top}-{self._digest()}" + ("-waves" if waves else "")
        self.dir = workdir / "images" / self._key

    def _digest(self) -> str:
        sources = self.sources
        h = hashlib.sha256(sources.key.encode() + sources.top.encode())
        for arg in [*map(str, PASTEL_HDL + PASTEL_CPP), *sources.args]:
            h.update(arg.encode())
            p = Path(arg)
            if not arg.startswith(("+", "-")) and p.is_file():
                h.update(_content_key(p))
        incdirs = [*PASTEL_INCDIRS]
        incdirs += [Path(a[len("+incdir+") :]) for a in sources.args if a.startswith("+incdir+")]
        for header in _headers(incdirs):
            h.update(str(header).encode())
            h.update(_content_key(header))
        tool = [
            "args",
            *self.extra_args,
            self.tool,
            shutil.which(self.executable) or self.executable,
            *(f"{v}={os.environ.get(v, '')}" for v in self.TOOL_ENV),
        ]
        h.update("\0".join(tool).encode())
        return h.hexdigest()[:12]

    @property
    def key(self) -> str:  # noqa: D102
        return self._key

    @property
    def build_log(self) -> Path:
        """The build's log: every step's command line, then its output."""
        return self.dir / "build.log"

    @property
    def build_rules(self) -> LogRules:
        """This image's build-log rules: the simulator's plus the device pack's."""
        return self.BUILD_RULES.merged(LogRules(ignore=(COMMAND_ECHO,))).merged(
            self.sources.build_log
        )

    @property
    def warnings(self) -> list[str]:
        """The build's warnings, ``"<line>: <text>"``, kept with the model."""
        path = self.dir / "warnings.txt"
        return path.read_text().splitlines() if path.is_file() else []

    @property
    @abstractmethod
    def exe(self) -> Path:
        """What runs the model: plusargs are appended to it. Built last."""

    @abstractmethod
    def steps(self) -> list[list[str]]:
        """The commands that build the model, run in order in :attr:`dir`."""

    def check_step(self, index: int, output: str) -> None:  # noqa: B027 - optional hook
        """Check step ``index``'s output (as written to the build log).

        Raise :class:`~soc_pastel.core.errors.ConfigurationError` if it failed
        despite exiting successfully (a batch wrapper may not pass on the
        tool's exit status).
        """

    def finish(self) -> None:  # noqa: B027 - optional hook
        """Called after every step succeeded, e.g. to write a run script."""

    def check_tool(self) -> None:
        """Fail early if the simulator isn't installed here."""
        if shutil.which(self.executable) is None:
            raise ConfigurationError(f"{self.executable} not found")

    def is_built(self) -> bool:  # noqa: D102
        return self.exe.is_file()

    def build(self) -> None:  # noqa: D102
        with file_lock(self.dir.with_suffix(".lock")):
            if self.is_built():
                return
            self.check_tool()
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "warnings.txt").unlink(missing_ok=True)
            log.info("building the %s model %s", self.tool, self.key)
            began = time.monotonic()
            with open(self.build_log, "w") as f:
                for index, cmd in enumerate(self.steps()):
                    # One line, so no part of the echo can match a log rule.
                    f.write("$ " + shlex.join(cmd).replace("\n", "\\n") + "\n\n")
                    f.flush()
                    start = f.tell()
                    result = subprocess.run(cmd, cwd=self.dir, stdout=f, stderr=subprocess.STDOUT)
                    f.flush()
                    if result.returncode != 0:
                        self._fail(f"exited with status {result.returncode}")
                    with open(self.build_log, errors="replace") as r:
                        r.seek(start)
                        self.check_step(index, r.read())
                    f.write("\n")
            findings = self.build_rules.check(self.build_log)
            if findings.failed:
                self._fail("reported errors", findings)
            if findings.warnings:
                (self.dir / "warnings.txt").write_text(
                    "".join(f"{n}: {line.strip()}\n" for n, line in findings.warnings)
                )
            self.finish()
            if not self._appeared():
                raise ConfigurationError(
                    f"{self.tool} build succeeded but {self.exe} does not exist after "
                    f"{self.APPEAR_TIMEOUT_S:.0f} s. If it was built on another machine, this "
                    f"one's file system hasn't seen it yet (see {self.build_log}):\n{self._tail()}"
                )
            n = len(self.warnings)
            log.info(
                "built the %s model in %.0f s%s",
                self.tool,
                time.monotonic() - began,
                f", with {n} warning{'s' if n != 1 else ''} (see {self.build_log})" if n else "",
            )

    def _tail(self) -> str:
        return self.build_log.read_text(errors="replace")[-3000:]

    def _fail(self, why: str, findings: LogFindings | None = None) -> None:
        """Fail the build, leaving nothing that looks like a built model."""
        self.exe.unlink(missing_ok=True)
        findings = findings or self.build_rules.check(self.build_log)
        detail = findings.describe() if findings.failed else self._tail()
        raise ConfigurationError(f"{self.tool} build {why} (see {self.build_log}):\n{detail}")

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


#: Models whose build warnings this process has already reported.
_REPORTED_WARNINGS: set[Path] = set()


class SimulatorPlatform(SimulationPlatform):
    """A simulation platform: builds a model, launches it and talks to it.

    The model runs through the launcher chosen with ``--sim-launcher`` or
    ``--sim-launcher-class`` (see :mod:`soc_pastel.sim.launch`), locally by
    default. A remote launcher needs the build directory and the run
    directory on a file system the simulation machine shares.

    Subclasses set :attr:`name` and :attr:`WAVES_FILE`, add their options,
    and build their image in :meth:`make_image`. Their work directory option
    is ``--<name>-workdir``, defaulting to ``.pastel/<name>``.
    """

    #: Waveform file written to the run directory when waves are enabled.
    WAVES_FILE: ClassVar[str] = "sim.vcd"
    #: Sidecar list written to the run directory for ``+pastel_sidecars``.
    SIDECARS_FILE: ClassVar[str] = "sidecars.tsv"
    #: The simulator's output, in the run directory.
    LOG_FILE: ClassVar[str] = "sim.log"
    #: What counts as an error or a warning in ``sim.log`` (see
    #: :mod:`soc_pastel.sim.logcheck`). Subclasses add their simulator's.
    RUN_RULES: ClassVar[LogRules] = COMMON_RUN_RULES
    #: Errors in ``sim.log`` that only follow from another error: the simulator
    #: reporting it stopped. Expected along with an expected error.
    STOP_MESSAGES: ClassVar[tuple[str, ...]] = ()

    platform_handlers: ClassVar[Mapping[str, type[EndpointHandler]]] = {
        PlatformControl.type_name: PlatformControl,
        Backdoor.type_name: Backdoor,
        Pipe.type_name: Pipe,
    }

    def __init__(self, device: Device, variant: Variant, options: Mapping[str, Any]) -> None:
        super().__init__(device, variant, options)
        workdir = options.get(f"{self.name}_workdir") or f".pastel/{self.name}"
        self.workdir = Path(workdir).resolve()
        self.waves = bool(options.get("waves"))
        self._image: SimulatorImage | None = None
        self._launcher: SimLauncher = make_launcher(options)
        self._proc: SimProcess | None = None
        self._session: SocketEndpointSession | None = None
        self._listener: Listener | None = None
        self._log = None
        self._ran = False
        #: The simulator's exit status once it has stopped, or None.
        self.exit_status: int | None = None
        self._forced = False  # stop() had to terminate or kill it

    @abstractmethod
    def make_image(self, sources: SimSources) -> SimulatorImage:
        """The image for these sources, with this platform's options."""

    def image(self) -> SimulatorImage:  # noqa: D102
        if self._image is None:
            sources = load_sim_sources(self.variant, self.workdir / "sources", self.name)
            self._image = self.make_image(sources)
        return self._image

    def option_args(self, name: str) -> tuple[str, ...]:
        """A shell-quoted string option (``--<platform>-args``) as arguments."""
        return tuple(shlex.split(self.options.get(name) or ""))

    @property
    def waves_path(self) -> Path | None:  # noqa: D102
        return self.run_dir / self.WAVES_FILE if self.waves else None

    @property
    def log_path(self) -> Path:
        """The simulator's output for this run."""
        return self.run_dir / self.LOG_FILE

    @property
    def run_rules(self) -> LogRules:
        """This run's log rules: the simulator's plus the device pack's."""
        return self.RUN_RULES.merged(self.image().sources.run_log)

    def check_run(self) -> LogFindings:
        """Check the finished run: its log, and how the simulator exited.

        A failing exit status counts as an error (line 0), unless PASTEL had to
        terminate the simulator itself. Returns no findings if it never ran.
        """
        if not self._ran:
            return LogFindings()
        findings = self.run_rules.check(self.log_path)
        if self.exit_status not in (None, 0) and not self._forced:
            findings.errors.insert(0, (0, f"{EXIT_STATUS_ERROR} {self.exit_status}"))
        return findings

    def capabilities(self) -> frozenset[str]:
        """Backdoor and simulation time, plus waves when run with ``--waves``."""
        features = super().capabilities()
        return features | {WAVES} if self.waves else features

    @property
    def endpoints(self) -> SocketEndpointSession:  # noqa: D102
        if self._session is None:
            raise TargetError("simulation is not running")
        return self._session

    def start(self) -> None:  # noqa: D102
        image = self.image()
        image.build()
        if image.warnings and image.dir not in _REPORTED_WARNINGS:
            _REPORTED_WARNINGS.add(image.dir)
            shown = "\n".join(f"  {w}" for w in image.warnings[:5])
            more = len(image.warnings) - 5
            log.warning(
                "the %s model has %d build warning%s (see %s):\n%s%s",
                self.name,
                len(image.warnings),
                "s" if len(image.warnings) != 1 else "",
                image.build_log,
                shown,
                f"\n  ... and {more} more" if more > 0 else "",
            )
        self._ran = True
        self.exit_status = None
        self._forced = False
        remote = self._launcher.remote
        self._listener = Listener(
            bind="" if remote else "127.0.0.1",
            advertise=self.options.get("sim_host_address") if remote else None,
        )
        run_dir = self.run_dir.resolve()
        log_path = run_dir / self.LOG_FILE
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
        log.info("simulator started; run directory %s", run_dir)
        began = time.monotonic()
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
        log.info("simulator connected after %.1f s", time.monotonic() - began)

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
                    self._forced = True
                    escalate()
            else:
                self._proc.wait()
            self.exit_status = self._proc.poll()
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
