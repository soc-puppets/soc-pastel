"""OpenOCD implementation of the on-chip debug contract."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from soc_pastel.core.errors import ConfigurationError, PastelTimeout, TargetError
from soc_pastel.core.memory import MemoryInterface
from soc_pastel.core.on_chip_debug import OnChipDebug, TargetState
from soc_pastel.core.platform import Sidecar, SimulationPlatform

from .config import OpenOcdConfig, fragment_path

if TYPE_CHECKING:
    from soc_pastel.core.harness import PlatformHarness
    from soc_pastel.sim.handlers import Pipe

_KEYS = {"impl", "transport", "chain", "cpu", "executable", "startup_timeout_s"}
_EOT = b"\x1a"
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_CFG, _LOG = "openocd.cfg", "openocd.log"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fragment(kind: str, value: str) -> str:
    """A shipped fragment by name, or inline Tcl."""
    if "\n" in value or " " in value:
        return value
    return fragment_path(kind, value).read_text()


def _log_tail(path: Path, chars: int = 1500) -> str:
    try:
        tail = path.read_text(errors="replace")[-chars:].strip()
    except OSError:
        return ""
    return f":\n{tail}" if tail else ""


class _SocketLink:
    """Tcl over OpenOCD's TCP Tcl port."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def request(self, data: bytes, timeout_s: float | None) -> bytes:
        self._sock.settimeout(timeout_s)
        self._sock.sendall(data + _EOT)
        buf = bytearray()
        try:
            while not buf.endswith(_EOT):
                chunk = self._sock.recv(65536)
                if not chunk:
                    raise TargetError("OpenOCD closed the connection")
                buf += chunk
        except TimeoutError:
            raise PastelTimeout(f"no reply from OpenOCD within {timeout_s} s") from None
        return bytes(buf[:-1])

    def shutdown(self) -> None:
        try:
            self._sock.sendall(b"shutdown" + _EOT)
        except OSError:
            pass
        self._sock.close()


class _PipeLink:
    """Tcl over OpenOCD's stdin and stdout, relayed by the simulator."""

    def __init__(self, pipe: Pipe) -> None:
        self._pipe = pipe

    def request(self, data: bytes, timeout_s: float | None) -> bytes:
        self._pipe.write(data + _EOT)
        return self._pipe.read_until(_EOT, timeout_s)[:-1]

    def shutdown(self) -> None:
        if self._pipe.closed:
            return
        try:
            self._pipe.write(b"shutdown" + _EOT)
            self._pipe.close_input()
        except Exception:  # the simulation may already be over
            pass


class OpenOcd(OnChipDebug):
    """On-chip debug through an OpenOCD process, driven over Tcl.

    Binding config keys:

    - ``impl``: ``"openocd"``.
    - ``transport``: name of a shipped transport fragment (``"remote_bitbang"``)
      or inline Tcl.
    - ``cpu``: name of a shipped CPU fragment (``"veer"``) or inline Tcl.
    - ``chain``: inline Tcl defining the TAP and creating ``$_TARGETNAME``.
    - ``executable``: the ``openocd`` binary; defaults to ``$PASTEL_OPENOCD``
      or ``openocd``.
    - ``startup_timeout_s``: how long to wait for OpenOCD to examine the target.

    **In simulation** OpenOCD is a :class:`~soc_pastel.core.platform.Sidecar`:
    the simulator starts it beside itself, on whichever machine it runs.
    ``remote_bitbang`` then reaches the testbench's JTAG server over a Unix
    socket on that machine, and OpenOCD serves Tcl on its stdin and stdout
    (``tcl_port pipe``), which the simulator relays over its connection to the
    test process. Only Tcl commands and replies cross the network, and
    OpenOCD opens no ports. The OpenOCD executable must exist on the
    simulation machine.

    **On hardware** OpenOCD runs beside the test process and is driven over a
    Tcl port on loopback. With ``remote_bitbang``, the bitbang server's
    address comes from the platform's
    :meth:`~soc_pastel.core.platform.Platform.debug_connection`.
    """

    impl_name: ClassVar[str] = "openocd"

    def __init__(self, name: str, config: Mapping[str, Any], harness: PlatformHarness) -> None:
        super().__init__(name, config, harness)
        unknown = set(config) - _KEYS
        if unknown:
            raise ConfigurationError(f"{name}: unknown openocd config keys {sorted(unknown)}")
        for key in ("transport", "chain", "cpu"):
            if key not in config:
                raise ConfigurationError(f"{name}: openocd config needs {key!r}")
        self._proc: subprocess.Popen | None = None
        self._link: _SocketLink | _PipeLink | None = None
        self._sidecar: str | None = None
        self._memory = _OcdMemory(self)

    @property
    def _in_simulation(self) -> bool:
        return isinstance(self.harness.platform, SimulationPlatform)

    @property
    def _executable(self) -> str:
        return self.config.get("executable") or os.environ.get("PASTEL_OPENOCD", "openocd")

    # -- Lifecycle ----------------------------------------------------------------------

    def render_config(self, tcl_port: int | None = None) -> str:
        """The complete OpenOCD configuration for this platform.

        Args:
            tcl_port: TCP port for Tcl, or None to serve Tcl on stdin and
                stdout (as a simulation sidecar).
        """
        transport = _fragment("transport", self.config["transport"])
        if self.config["transport"] == "remote_bitbang":
            if self._in_simulation:
                # Exported by pastel_jtag inside the simulator, whose child this is.
                transport += (
                    "\nremote_bitbang port 0\nremote_bitbang host $::env(PASTEL_JTAG_SOCKET)\n"
                )
            else:
                conn = self.harness.platform.debug_connection().get("remote_bitbang")
                if conn is None:
                    raise ConfigurationError("platform offers no remote_bitbang connection")
                host, port = conn
                transport += f"\nremote_bitbang host {host}\nremote_bitbang port {port}\n"
        return OpenOcdConfig(
            transport=transport,
            chain=self.config["chain"],
            cpu=_fragment("cpu", self.config["cpu"]),
            # The underscore spellings: newer OpenOCD keeps them as aliases, and
            # older builds (some RISC-V forks) have no `gdb port` form at all.
            extra=[
                "gdb_port disabled",
                "telnet_port disabled",
                f"tcl_port {'pipe' if tcl_port is None else tcl_port}",
                "init",
            ],
        ).render()

    def sim_sidecars(self) -> Iterable[Sidecar]:
        """In simulation, OpenOCD itself, started by the simulator."""
        if not self._in_simulation:
            return ()
        (self.harness.platform.run_dir / _CFG).write_text(self.render_config())
        self._sidecar = f"{self.name}.openocd"
        return (Sidecar(self._sidecar, (self._executable, "-f", _CFG), _LOG),)

    def open(self) -> None:  # noqa: D102
        timeout_s = float(self.config.get("startup_timeout_s", 120))
        log = self.harness.platform.run_dir / _LOG
        if self._sidecar is not None:
            self._open_sidecar(timeout_s, log)
        else:
            self._open_local(timeout_s, log)

    def _open_sidecar(self, timeout_s: float, log: Path) -> None:
        from soc_pastel.sim.handlers import PipeClosed

        platform = self.harness.platform
        assert isinstance(platform, SimulationPlatform)
        self._link = _PipeLink(platform.endpoints.handler(self._sidecar))  # type: ignore[arg-type]
        try:
            self._target = self.tcl("target current", timeout_s=timeout_s)
        except PipeClosed as e:
            self._link = None
            hint = " (is it installed on the simulation machine?)" if e.exit_status == 127 else ""
            raise TargetError(
                f"OpenOCD exited during start-up{hint}: {e}; see {log}" + _log_tail(log)
            ) from None
        except PastelTimeout:
            raise PastelTimeout(
                f"OpenOCD did not start in time; see {log}" + _log_tail(log)
            ) from None

    def _open_local(self, timeout_s: float, log: Path) -> None:
        run_dir = self.harness.platform.run_dir
        port = _free_port()
        (run_dir / _CFG).write_text(self.render_config(port))
        self._log = open(log, "w")
        try:
            self._proc = subprocess.Popen(
                [self._executable, "-f", _CFG],
                stdout=self._log,
                stderr=subprocess.STDOUT,
                cwd=run_dir,
            )
        except FileNotFoundError:
            raise ConfigurationError(f"OpenOCD executable {self._executable!r} not found") from None
        deadline = time.monotonic() + timeout_s
        sock = None
        while sock is None:
            if self._proc.poll() is not None:
                raise TargetError(f"OpenOCD exited during start-up; see {log}" + _log_tail(log))
            if time.monotonic() > deadline:
                raise PastelTimeout("OpenOCD did not start in time")
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=1)
            except OSError:
                time.sleep(0.2)
        self._link = _SocketLink(sock)
        self._target = self.tcl("target current")

    def close(self) -> None:  # noqa: D102
        if self._link is not None:
            self._link.shutdown()
            self._link = None
        if self._proc is not None:
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None
            self._log.close()

    # -- Tcl ----------------------------------------------------------------------------

    def tcl(self, command: str, timeout_s: float | None = None) -> str:
        """Run a Tcl command in OpenOCD and return its result.

        Raises:
            TargetError: The command failed.
        """
        if self._link is None:
            raise TargetError("OpenOCD is not running")
        wrapped = (
            f'if {{[catch {{{command}}} __r]}} {{set __o "ERR:$__r"}} else {{set __o "OK:$__r"}}'
        )
        reply = self._link.request(wrapped.encode(), timeout_s).decode(errors="replace")
        if reply.startswith("OK:"):
            return reply[3:].strip()
        shown = command if len(command) < 120 else command[:117] + "..."
        raise TargetError(f"OpenOCD: {shown!r} failed: {reply[4:].strip()}")

    # -- OnChipDebug ------------------------------------------------------------------

    def reset(self, *, halt: bool = True) -> None:  # noqa: D102
        self.tcl("reset halt" if halt else "reset run")
        if halt and self.state() is not TargetState.HALTED:
            raise TargetError("target did not halt after reset")

    def halt(self) -> None:  # noqa: D102
        self.tcl("halt")
        if self.state() is not TargetState.HALTED:
            raise TargetError("target did not halt")

    def resume(self, pc: int | None = None) -> None:  # noqa: D102
        self.tcl("resume" if pc is None else f"resume {pc:#x}")

    def step(self) -> None:  # noqa: D102
        self.tcl("step")

    def state(self) -> TargetState:  # noqa: D102
        s = self.tcl(f"{self._target} curstate")
        return {
            "running": TargetState.RUNNING,
            "halted": TargetState.HALTED,
            "reset": TargetState.RESET,
        }.get(s, TargetState.UNKNOWN)

    def read_register(self, name: str) -> int:  # noqa: D102
        values = _HEX.findall(self.tcl(f"reg {name}"))
        if not values:
            raise TargetError(f"could not read register {name}")
        return int(values[-1], 16)

    def write_register(self, name: str, value: int) -> None:  # noqa: D102
        self.tcl(f"reg {name} {value:#x}")

    @property
    def memory(self) -> MemoryInterface:  # noqa: D102
        return self._memory

    def add_breakpoint(self, addr: int, *, hardware: bool = True) -> int:  # noqa: D102
        self.tcl(f"bp {addr:#x} 4" + (" hw" if hardware else ""))
        return addr

    def remove_breakpoint(self, breakpoint_id: int) -> None:  # noqa: D102
        self.tcl(f"rbp {breakpoint_id:#x}")


class _OcdMemory(MemoryInterface):
    """Memory access through OpenOCD's read_memory/write_memory.

    Always uses aligned 32-bit accesses, read-modify-writing partial words at
    the edges: some memories (VeeR's ICCM, for one) reject byte accesses over
    debug.
    """

    name = "on_chip_debug"
    WORDS_PER_CMD = 256

    def __init__(self, ocd: OpenOcd) -> None:
        self._ocd = ocd

    def _read_words(self, addr: int, count: int) -> bytes:
        out = bytearray()
        for start in range(0, count, self.WORDS_PER_CMD):
            n = min(self.WORDS_PER_CMD, count - start)
            words = self._ocd.tcl(f"read_memory {addr + 4 * start:#x} 32 {n}").split()
            out += b"".join(int(w, 16).to_bytes(4, "little") for w in words)
        return bytes(out)

    def _write_words(self, addr: int, data: bytes) -> None:
        words = [int.from_bytes(data[i : i + 4], "little") for i in range(0, len(data), 4)]
        for start in range(0, len(words), self.WORDS_PER_CMD):
            chunk = " ".join(f"{w:#x}" for w in words[start : start + self.WORDS_PER_CMD])
            self._ocd.tcl(f"write_memory {addr + 4 * start:#x} 32 {{{chunk}}}")

    def read(self, addr: int, length: int) -> bytes:  # noqa: D102
        start, end = addr & ~3, (addr + length + 3) & ~3
        data = self._read_words(start, (end - start) // 4)
        return data[addr - start : addr - start + length]

    def write(self, addr: int, data: bytes) -> None:  # noqa: D102
        if not data:
            return
        start, end = addr & ~3, (addr + len(data) + 3) & ~3
        buf = bytearray(end - start)
        if addr != start:
            buf[0:4] = self._read_words(start, 1)
        if addr + len(data) != end:
            buf[-4:] = self._read_words(end - 4, 1)
        buf[addr - start : addr - start + len(data)] = data
        self._write_words(start, bytes(buf))
