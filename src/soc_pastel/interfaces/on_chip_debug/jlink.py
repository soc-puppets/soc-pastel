"""On-chip debug through a SEGGER J-Link.

One driver, ``jlink``, with two ways of talking to the probe (*backends*):

- ``commander`` (the default): drives J-Link Commander (``JLinkExe``) under
  :mod:`pexpect`, sending Commander commands and parsing the replies. Ported
  from APE's ``jlink_exe_wrapper``.
- ``pylink``: calls SEGGER's J-Link library (``libjlinkarm``) directly through
  `pylink <https://pylink.readthedocs.io>`_ (``pylink-square``). No text
  parsing, and faster for many small accesses.

Both need SEGGER's J-Link software installed, and
``pip install "soc-pastel[jlink]"`` (which brings ``pexpect`` and
``pylink-square``). Everything else (the halt checks, memory access, locking,
the voltage check, ``jlink.log``) is shared, so the two behave the same.

Bind it for a hardware platform::

    "on_chip_debug": InterfaceBinding(type="on_chip_debug", impls={
        "fpga": {"impl": "jlink", "device": "RISC-V", "interface": "JTAG", "speed": 4000},
    })

Binding config keys:

- ``impl``: ``"jlink"``.
- ``backend``: ``"commander"`` (default) or ``"pylink"``. ``--jlink-backend``
  overrides it at run time.
- ``device``: the J-Link device name, e.g. ``"RISC-V"``.
- ``interface``: ``"JTAG"`` (default) or ``"SWD"``.
- ``speed``: interface speed in kHz (default 4000).
- ``ip``: connect to a J-Link over IP, e.g. ``"192.168.1.20"``, or through a
  SEGGER tunnel, ``"tunnel:<name or serial>[:<password>[:<server>[:<port>]]]"``
  (the password is masked in logs and messages). ``serial``: pick a USB J-Link by
  serial number. Neither: the only USB J-Link. Both can be given at run time
  with ``--jlink-ip`` and ``--jlink-serial``.
- ``jtag_conf``: the JTAG chain position, ``"<IRPre> <DRPre>"`` (default
  ``"-1 -1"``: auto-detect).
- ``reset_pin``: also pulse the nRESET pin when resetting (default False).
- ``min_vtref``: fail to connect if the target's reference voltage is lower
  (default 1.0 V; ``0`` skips the check).
- ``timeout_s``: how long one Commander command, or opening the probe and
  connecting through pylink, may take (default 30).
- ``executable`` (commander): J-Link Commander (default ``$PASTEL_JLINKEXE``
  or ``JLinkExe``).
- ``library`` (pylink): SEGGER's J-Link library (default
  ``$PASTEL_JLINK_LIBRARY``, else wherever pylink finds it).

A binding may set both ``executable`` and ``library``, so either backend can
be chosen at run time; the one not in use is ignored.
"""

from __future__ import annotations

import abc
import ctypes
import logging
import os
import re
import struct
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from soc_pastel.core.errors import ConfigurationError, PastelTimeout, TargetError
from soc_pastel.core.memory import MemoryInterface, WordMemory
from soc_pastel.core.on_chip_debug import OnChipDebug, TargetState
from soc_pastel.core.riscv import ABI_NAMES, ALIASES, gpr_number

if TYPE_CHECKING:
    from soc_pastel.core.harness import PlatformHarness

log = logging.getLogger(__name__)

_KEYS = {
    "impl", "backend", "device", "interface", "speed", "ip", "serial", "jtag_conf",
    "reset_pin", "min_vtref", "timeout_s", "executable", "library",
}  # fmt: skip
#: Commander's error messages, e.g. ``****** Error: ...``, ``ERROR: ...`` or
#: ``Connecting to J-Link via USB...FAILED: Cannot connect to J-Link.``
_ERROR = re.compile(r"^\s*(\*+\s*Error\b|ERROR\b|Could not)|\bFAILED:", re.I | re.M)
T = TypeVar("T")


def _redact(ip: str) -> str:
    """``ip`` for messages and logs: a tunnel's password replaced by ``***``.

    A tunnel is ``tunnel:<name or serial>[:<password>[:<server>[:<port>]]]``.
    """
    fields = str(ip).split(":")
    if fields[0].lower() == "tunnel" and len(fields) > 2 and fields[2]:
        fields[2] = "***"
    return ":".join(fields)


def _no_probe(ip: str | None, serial: str | None, detail: str) -> TargetError:
    where = f"at {_redact(ip)}" if ip else f"with serial {serial}" if serial else "on USB"
    return TargetError(
        f"can't reach a J-Link {where} (pick one with --jlink-serial or --jlink-ip):\n"
        f"{detail.strip()}"
    )


class JLinkBackend(abc.ABC):
    """How the ``jlink`` driver talks to the probe: the operations it needs.

    The driver serialises calls, checks what needs checking (e.g. that the CPU
    halted) and does everything else; a backend only carries operations out,
    raising :class:`TargetError` when one fails.

    Args:
        ip: Probe IP address, or None.
        serial: USB serial number, or None.
        timeout_s: How long one operation may take.
        log_path: Where to keep the probe software's output.
    """

    #: The ``backend`` config value that selects it.
    name: ClassVar[str]

    def __init__(
        self,
        *,
        ip: str | None = None,
        serial: str | None = None,
        timeout_s: float = 30.0,
        log_path: Path | None = None,
    ) -> None:
        self.ip = ip
        self.serial = serial
        self.timeout_s = timeout_s
        self.log_path = log_path

    @abc.abstractmethod
    def start(self) -> None:
        """Open the probe (not yet the target)."""

    @abc.abstractmethod
    def close(self) -> None:
        """Let the probe go. Safe to call more than once, or after a failed start."""

    @abc.abstractmethod
    def connect(self, device: str, interface: str, speed: int, jtag_conf: str) -> None:
        """Select the device and interface, and connect to the target."""

    @abc.abstractmethod
    def vtref(self) -> float:
        """The target's reference voltage, in volts."""

    @abc.abstractmethod
    def is_halted(self) -> bool:
        """True if the CPU is halted."""

    @abc.abstractmethod
    def halt(self) -> None:
        """Ask the CPU to halt."""

    @abc.abstractmethod
    def go(self) -> None:
        """Let the CPU run."""

    @abc.abstractmethod
    def step(self) -> None:
        """Execute one instruction."""

    @abc.abstractmethod
    def reset(self) -> None:
        """Reset the target, leaving the CPU halted."""

    @abc.abstractmethod
    def pulse_reset_pin(self) -> None:
        """Drive nRESET low, then high again."""

    @abc.abstractmethod
    def read_register(self, name: str) -> int:
        """Read a CPU register by name."""

    @abc.abstractmethod
    def write_register(self, name: str, value: int) -> None:
        """Write a CPU register by name."""

    @abc.abstractmethod
    def read_words(self, addr: int, count: int) -> bytes:
        """Read ``count`` aligned 32-bit words."""

    @abc.abstractmethod
    def write_words(self, addr: int, data: bytes) -> None:
        """Write aligned 32-bit words (``len(data)`` is a multiple of 4)."""

    @abc.abstractmethod
    def set_breakpoint(self, addr: int, hardware: bool) -> int:
        """Set a breakpoint; return a handle for :meth:`clear_breakpoint`."""

    @abc.abstractmethod
    def clear_breakpoint(self, handle: int) -> None:
        """Remove a breakpoint set by :meth:`set_breakpoint`."""


# -- J-Link Commander -----------------------------------------------------------------------


class JLinkCommander(JLinkBackend):
    """J-Link Commander (``JLinkExe``), driven over pexpect. From APE's wrapper.

    Args:
        executable: ``JLinkExe``.
        ip: ``-IP`` value (IP address or ``tunnel:<name>``), or None.
        serial: USB serial number (``-SelectEmuBySN``), or None.
        timeout_s: How long one command may take.
        log_path: Where to keep everything Commander printed.
    """

    name: ClassVar[str] = "commander"
    PROMPT = "J-Link>"
    #: Above this many words, go through a file (savebin/loadbin) instead.
    BULK_WORDS = 64

    def __init__(self, executable: str = "JLinkExe", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.executable = executable
        self._proc: Any = None
        self._log_file: Any = None

    def start(self) -> None:
        """Start Commander and wait for its prompt."""
        try:
            import pexpect
        except ImportError:
            raise ConfigurationError(
                'the jlink commander backend needs pexpect: pip install "soc-pastel[jlink]"'
            ) from None
        args = ["-NoGui", "1"]
        if self.ip:
            args += ["-IP", str(self.ip)]
        if self.serial:
            args += ["-SelectEmuBySN", str(self.serial)]
        try:
            self._proc = pexpect.spawn(self.executable, args, timeout=self.timeout_s)
        except pexpect.ExceptionPexpect as e:
            raise ConfigurationError(f"cannot start {self.executable}: {e}") from None
        if self.log_path is not None:
            self._log_file = open(self.log_path, "wb")
            self._proc.logfile_read = self._log_file
        banner = self._expect_prompt("starting J-Link Commander")
        if _ERROR.search(banner):
            raise _no_probe(self.ip, self.serial, f"J-Link Commander says:\n{banner}")

    def close(self) -> None:
        """Leave Commander."""
        if self._proc is not None:
            try:
                self._proc.sendline("exit")
                self._proc.expect([self._eof(), self.PROMPT], timeout=5)
            except Exception:
                pass
            self._proc.close(force=True)
            self._proc = None
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def command(self, cmd: str, *, check: bool = True) -> str:
        """Send one command; return what Commander printed before the next prompt.

        Raises:
            TargetError: Commander reported an error (unless ``check=False``).
        """
        if self._proc is None:
            raise TargetError("J-Link Commander is not running")
        self._proc.sendline(cmd)
        out = self._expect_prompt(cmd)
        if check and _ERROR.search(out):
            raise TargetError(f"J-Link: {cmd!r} failed:\n{out.strip()}")
        return out

    def _expect_prompt(self, what: str) -> str:
        import pexpect

        try:
            self._proc.expect(self.PROMPT)
        except pexpect.TIMEOUT:
            raise PastelTimeout(f"J-Link: no prompt after {what!r}") from None
        except pexpect.EOF:
            before = self._proc.before
            text = before.decode(errors="replace") if isinstance(before, bytes) else before
            raise TargetError(f"J-Link Commander exited during {what!r}:\n{text}") from None
        out = self._proc.before
        return out.decode("utf-8", errors="replace") if isinstance(out, bytes) else out

    @staticmethod
    def _eof() -> Any:
        import pexpect

        return pexpect.EOF

    # -- Operations (as APE's wrapper) ---------------------------------------------------

    def connect(self, device: str, interface: str, speed: int, jtag_conf: str) -> None:  # noqa: D102
        for cmd in (f"device {device}", f"if {interface}", f"speed {speed}"):
            self.command(cmd)
        if interface.upper() == "JTAG":
            self.command(f"JTAGConf {jtag_conf}")
        self.command("connect")

    def vtref(self) -> float:
        """The target's reference voltage, from ``ShowHWStatus``."""
        out = self.command("ShowHWStatus")
        match = re.search(r"VTref\s*=\s*([\d.]+)\s*V", out)
        if not match:
            raise TargetError(f"J-Link: cannot read VTref from:\n{out.strip()}")
        return float(match.group(1))

    def is_halted(self) -> bool:
        """True if the CPU is halted (``IsHalted``)."""
        out = self.command("IsHalted").lower()
        if "cpu is not halted" in out:
            return False
        if "cpu is halted" in out:
            return True
        raise TargetError(f"J-Link: cannot tell whether the CPU is halted from:\n{out.strip()}")

    def halt(self) -> None:  # noqa: D102
        self.command("h")

    def go(self) -> None:  # noqa: D102
        self.command("g")

    def step(self) -> None:  # noqa: D102
        self.command("s")

    def reset(self) -> None:  # noqa: D102
        self.command("r")  # Commander's reset halts the CPU

    def pulse_reset_pin(self) -> None:  # noqa: D102
        self.command("clrreset")
        self.command("setreset")

    def _by_name(self, name: str, op: Callable[[str], T]) -> T:
        """``op(name)``; if Commander doesn't know a RISC-V ABI name (``a0``), ``op("X10")``."""
        try:
            return op(name)
        except TargetError:
            number = gpr_number(name)
            if number is None or name.upper() == f"X{number}":
                raise
            return op(f"X{number}")

    def read_register(self, name: str) -> int:
        """Read a register (``rreg``)."""

        def rreg(reg: str) -> int:
            out = self.command(f"rreg {reg}")
            match = re.search(rf"\b{re.escape(reg)}\s*=\s*(?:0x)?([0-9A-Fa-f]+)", out, re.I)
            if not match:
                raise TargetError(f"J-Link: cannot read register {reg} from:\n{out.strip()}")
            return int(match.group(1), 16)

        return self._by_name(name, rreg)

    def write_register(self, name: str, value: int) -> None:
        """Write a register (``setpc`` for the PC, as APE did; ``wreg`` otherwise)."""
        if name.lower() == "pc":
            self.command(f"setpc 0x{value:X}")
        else:
            self._by_name(name, lambda reg: self.command(f"wreg {reg} 0x{value:X}"))

    def read_words(self, addr: int, count: int) -> bytes:  # noqa: D102
        if count > self.BULK_WORDS:
            return self.savebin(addr, 4 * count)
        return struct.pack(f"<{count}I", *self.mem32(addr, count))

    def write_words(self, addr: int, data: bytes) -> None:  # noqa: D102
        if len(data) > 4 * self.BULK_WORDS:
            self.loadbin(addr, data)
        else:
            self.write32(addr, list(struct.unpack(f"<{len(data) // 4}I", data)))

    def set_breakpoint(self, addr: int, hardware: bool) -> int:
        """``setbp``; the handle is Commander's, or -1 if it didn't say."""
        out = self.command(f"setbp 0x{addr:X}" + (" H" if hardware else " S"))
        match = re.search(r"handle\s*=?\s*(?:0x)?([0-9A-Fa-f]+)", out, re.I)
        return int(match.group(1), 16) if match else -1

    def clear_breakpoint(self, handle: int) -> None:  # noqa: D102
        self.command(f"clrbp {handle:X}" if handle >= 0 else "clrbp")

    def mem32(self, addr: int, count: int) -> list[int]:
        """Read ``count`` words (``mem32``)."""
        out = self.command(f"mem32 0x{addr:X} 0x{count:X}")
        words: list[int] = []
        for match in re.finditer(r"^\s*[0-9A-Fa-f]+\s*=\s*(.+)$", out, re.M):
            words += [int(token, 16) for token in match.group(1).split()]
        if len(words) < count:
            raise TargetError(f"J-Link: short read at {addr:#x}:\n{out.strip()}")
        return words[:count]

    def savebin(self, addr: int, length: int) -> bytes:
        """Read ``length`` bytes through a temporary file (``savebin``): fast for bulk."""
        fd, path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)
        try:
            self.command(f'savebin "{path}" 0x{addr:X} 0x{length:X}')
            return Path(path).read_bytes()[:length]
        finally:
            os.unlink(path)

    def write32(self, addr: int, words: list[int]) -> None:
        """Write words, a few per command (``write4``, as APE did)."""
        for start in range(0, len(words), 8):
            chunk = " ".join(f"0x{w:X}" for w in words[start : start + 8])
            self.command(f"write4 0x{addr + 4 * start:X} {chunk}")

    def loadbin(self, addr: int, data: bytes) -> None:
        """Write bytes through a temporary file (``loadbin``): fast for bulk."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(data)
            path = f.name
        try:
            self.command(f'loadbin "{path}" 0x{addr:X}')
        finally:
            os.unlink(path)


# -- pylink ---------------------------------------------------------------------------------


class JLinkPylink(JLinkBackend):
    """SEGGER's J-Link library, called through pylink (``pylink-square``).

    The library's own log goes to ``log_path``, with a ``>`` line for each
    call. Opening a probe that isn't there can block inside the library for
    minutes, so opening and connecting give up after ``timeout_s``.

    Args:
        library: Path to the J-Link library, or None to let pylink find it.
        ip: Probe IP address or ``tunnel:<name>``, with ``:<port>`` if not
            19020; or None.
        serial: USB serial number, or None.
        timeout_s: How long opening the probe, and connecting, may take.
        log_path: Where to keep the library's log.
    """

    name: ClassVar[str] = "pylink"
    DEFAULT_PORT = 19020

    def __init__(self, library: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.library = library
        self._pylink: Any = None
        self._jl: Any = None
        self._log_file: Any = None
        self._registers: dict[str, int] = {}

    def _log(self, text: str) -> None:
        if self._log_file is not None:
            self._log_file.write(text.rstrip("\n") + "\n")
            self._log_file.flush()

    def _call(self, what: str, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Call into pylink, logging the call and turning its errors into TargetError."""
        self._log(f"> {what}")
        try:
            return fn(*args, **kwargs)
        except self._pylink.errors.JLinkException as e:
            self._log(f"! {e}")
            raise TargetError(f"J-Link: {what} failed: {e}") from None

    def _bounded(self, what: str, fn: Callable[[], T]) -> T:
        """Call ``fn`` in a thread, giving up after ``timeout_s`` (the library can hang)."""
        result: list[Any] = []
        error: list[BaseException] = []

        def run() -> None:
            try:
                result.append(fn())
            except BaseException as e:  # re-raised below, in the caller's thread
                error.append(e)

        worker = threading.Thread(target=run, name=f"pylink {what}", daemon=True)
        worker.start()
        worker.join(self.timeout_s)
        if worker.is_alive():
            # Stuck in the library: abandon the (daemon) thread, and the link with it.
            self._jl = None
            raise _no_probe(
                self.ip, self.serial,
                f"the J-Link library did not return from {what} within {self.timeout_s:g} s",
            )  # fmt: skip
        if error:
            raise error[0]
        return result[0]

    def start(self) -> None:
        """Load the library and open the probe."""
        try:
            import pylink
        except ImportError:
            raise ConfigurationError(
                'the jlink pylink backend needs pylink-square: pip install "soc-pastel[jlink]"'
            ) from None
        self._pylink = pylink
        if self.log_path is not None:
            self._log_file = open(self.log_path, "w")
        library = self.library or os.environ.get("PASTEL_JLINK_LIBRARY")
        try:
            lib = pylink.library.Library(library) if library else None
            self._jl = pylink.JLink(
                lib=lib,
                log=self._log,
                detailed_log=lambda s: None,
                error=lambda s: self._log(f"ERROR: {s}"),
                warn=lambda s: self._log(f"WARNING: {s}"),
            )
        except (OSError, TypeError) as e:
            raise ConfigurationError(
                f"cannot load SEGGER's J-Link library ({library or 'as found by pylink'}): "
                f"{e or 'not found'}. Install the J-Link software, or set the binding's "
                "'library' or $PASTEL_JLINK_LIBRARY"
            ) from None
        ip_addr = self.ip_addr(self.ip) if self.ip else None
        serial = int(self.serial) if self.serial else None
        shown = _redact(ip_addr) if ip_addr else None
        self._log(f"> open(serial_no={serial}, ip_addr={shown})")
        jl = self._jl
        try:
            self._bounded("open()", lambda: jl.open(serial_no=serial, ip_addr=ip_addr))
        except pylink.errors.JLinkException as e:
            raise _no_probe(self.ip, self.serial, str(e)) from None

    @classmethod
    def ip_addr(cls, ip: str) -> str:
        """``ip`` as pylink's ``open()`` takes it: ``<address>:<port>``.

        pylink splits off the last field as the port, and hands the library
        the rest. A tunnel, ``tunnel:<name or serial>[:<password>[:<server>
        [:<port>]]]`` as Commander's ``-IP`` takes it, has its port only as a
        fifth field, so anything shorter gets one added (as pylink's
        ``open_tunnel()`` does).
        """
        ip = str(ip)
        fields = ip.split(":")
        if fields[0].lower() == "tunnel":
            has_port = len(fields) >= 5 and fields[4].isdigit()
        else:
            has_port = len(fields) == 2
        return ip if has_port else f"{ip}:{cls.DEFAULT_PORT}"

    def close(self) -> None:  # noqa: D102
        jl, self._jl = self._jl, None
        if jl is not None:
            try:
                jl.close()
            except Exception:
                pass
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _link(self) -> Any:
        if self._jl is None:
            raise TargetError("the J-Link is not open")
        return self._jl

    def connect(self, device: str, interface: str, speed: int, jtag_conf: str) -> None:  # noqa: D102
        jl = self._link()
        tif = getattr(self._pylink.enums.JLinkInterfaces, interface.upper(), None)
        if tif is None:
            raise ConfigurationError(f"jlink: unknown interface {interface!r}")
        self._call(f"set_tif({interface})", jl.set_tif, tif)
        if interface.upper() == "JTAG":
            pre = [int(v) for v in jtag_conf.split()]
            if pre != [-1, -1]:  # -1 -1: let the library find the device on the chain
                self._call(f"jtag_configure({jtag_conf})", jl.jtag_configure, *pre)
        self._bounded(
            f"connect({device})",
            lambda: self._call(f"connect({device}, speed={speed})", jl.connect, device, speed),
        )
        self._registers = {}
        for index in self._register_list():
            name = jl.register_name(index)
            if "?" not in name:  # "R??": an index the target doesn't implement
                self._registers.setdefault(name.lower(), index)
        # The library names RISC-V's integer registers X0-X31; add their ABI names.
        for alias in (*ABI_NAMES, *ALIASES):
            number = gpr_number(alias)
            if f"x{number}" in self._registers:
                self._registers.setdefault(alias, self._registers[f"x{number}"])

    def _register_list(self) -> list[int]:
        """Every register index the library has for the target.

        pylink's ``register_list()`` fetches at most 256, but a RISC-V
        target has thousands (the CSRs come first, by CSR number; X0-X31
        are at 4096 and the PC after them), so ask the library directly.
        """
        get_list = self._link()._dll.JLINKARM_GetRegisterList
        size = 256
        while True:
            buf = (ctypes.c_uint32 * size)()
            count = self._call(f"JLINKARM_GetRegisterList({size})", get_list, buf, size)
            if count < size or size >= 1 << 16:
                return list(buf[:count])
            size *= 4

    def vtref(self) -> float:  # noqa: D102
        status = self._call("hardware_status", lambda: self._link().hardware_status)
        return status.voltage / 1000.0

    def is_halted(self) -> bool:  # noqa: D102
        return bool(self._call("halted()", self._link().halted))

    def halt(self) -> None:
        """Halt (``JLINKARM_Halt``, not pylink's ``halt()``, which then sleeps for 1 s)."""
        self._call("JLINKARM_Halt()", self._link()._dll.JLINKARM_Halt)

    def go(self) -> None:
        """Let the CPU run (``restart()``, which does nothing if it is already running)."""
        self._call("restart()", self._link().restart)

    def step(self) -> None:  # noqa: D102
        self._call("step()", self._link().step)

    def reset(self) -> None:  # noqa: D102
        self._call("reset(halt=True)", self._link().reset, halt=True)

    def pulse_reset_pin(self) -> None:  # noqa: D102
        jl = self._link()
        self._call("set_reset_pin_low()", jl.set_reset_pin_low)
        time.sleep(0.01)
        self._call("set_reset_pin_high()", jl.set_reset_pin_high)

    def _register(self, name: str) -> int:
        try:
            return self._registers[name.lower()]
        except KeyError:
            known = sorted(self._registers)
            sample = ", ".join(known[:20]) + (", ..." if len(known) > 20 else "")
            raise TargetError(
                f"J-Link: register {name} not found (the library knows {len(known)}: "
                f"{sample or 'none'})"
            ) from None

    def read_register(self, name: str) -> int:  # noqa: D102
        index = self._register(name)
        value = self._call(f"register_read({name})", self._link().register_read, index)
        return int(value) & 0xFFFF_FFFF  # pylink hands back the library's U32 as a signed int

    def write_register(self, name: str, value: int) -> None:  # noqa: D102
        index = self._register(name)
        self._call(f"register_write({name}, {value:#x})", self._link().register_write, index, value)

    def read_words(self, addr: int, count: int) -> bytes:  # noqa: D102
        words = self._call(
            f"memory_read32({addr:#x}, {count})", self._link().memory_read32, addr, count
        )
        if len(words) != count:  # the library read less, without saying why
            raise TargetError(
                f"J-Link: could only read {len(words)} of {count} words at {addr:#x}: "
                "is there memory there?"
            )
        return struct.pack(f"<{count}I", *words)

    def write_words(self, addr: int, data: bytes) -> None:  # noqa: D102
        words = list(struct.unpack(f"<{len(data) // 4}I", data))
        self._call(
            f"memory_write32({addr:#x}, {len(words)} words)",
            self._link().memory_write32, addr, words,
        )  # fmt: skip

    def set_breakpoint(self, addr: int, hardware: bool) -> int:  # noqa: D102
        jl = self._link()
        kind = "hardware" if hardware else "software"
        setter = jl.hardware_breakpoint_set if hardware else jl.software_breakpoint_set
        return int(self._call(f"{kind}_breakpoint_set({addr:#x})", setter, addr))

    def clear_breakpoint(self, handle: int) -> None:  # noqa: D102
        if not self._call(f"breakpoint_clear({handle})", self._link().breakpoint_clear, handle):
            raise TargetError(f"J-Link: could not clear breakpoint {handle}")


#: The backends, by ``backend`` config value.
BACKENDS: dict[str, type[JLinkBackend]] = {b.name: b for b in (JLinkCommander, JLinkPylink)}


# -- The driver -----------------------------------------------------------------------------


class _JLinkMemory(WordMemory):
    """Target memory through the J-Link, in aligned 32-bit words."""

    def __init__(self, jlink: JLink) -> None:
        self._jlink = jlink

    def _read_words(self, addr: int, count: int) -> bytes:
        with self._jlink._lock:
            return self._jlink._backend().read_words(addr, count)

    def _write_words(self, addr: int, data: bytes) -> None:
        with self._jlink._lock:
            self._jlink._backend().write_words(addr, data)


class JLink(OnChipDebug):
    """On-chip debug through a SEGGER J-Link, by Commander or by pylink.

    For hardware platforms (FPGA boards, silicon). The probe software's output
    goes to ``jlink.log`` in the test's run directory. Operations are
    serialised, so the firmware runner can poll memory while a test calls in
    from a callback.
    """

    impl_name: ClassVar[str] = "jlink"

    def __init__(self, name: str, config: Mapping[str, Any], harness: PlatformHarness) -> None:
        super().__init__(name, config, harness)
        unknown = set(config) - _KEYS
        if unknown:
            raise ConfigurationError(f"{name}: unknown jlink config keys {sorted(unknown)}")
        if "device" not in config:
            raise ConfigurationError(
                f"{name}: jlink config needs 'device' (the J-Link device name)"
            )
        self._backend_class(config.get("backend", "commander"), "its 'backend'")
        self._lock = threading.RLock()
        self._jl: JLinkBackend | None = None
        self._memory = _JLinkMemory(self)
        self._bp_handles: dict[int, int] = {}  # address -> the backend's handle

    def _backend_class(self, backend: str, source: str) -> type[JLinkBackend]:
        try:
            return BACKENDS[backend]
        except KeyError:
            raise ConfigurationError(
                f"{self.name}: unknown jlink backend {backend!r} (from {source}); "
                f"choose from {', '.join(BACKENDS)}"
            ) from None

    def _backend(self) -> JLinkBackend:
        if self._jl is None:
            raise TargetError("the J-Link is not connected")
        return self._jl

    @property
    def backend(self) -> str:
        """The backend in use (or to be used): ``--jlink-backend``, else the binding's."""
        options = self.harness.platform.options
        return options.get("jlink_backend") or self.config.get("backend", "commander")

    # -- Lifecycle ----------------------------------------------------------------------

    def open(self) -> None:  # noqa: D102
        options = self.harness.platform.options
        cfg = self.config
        source = "--jlink-backend" if options.get("jlink_backend") else "its 'backend'"
        backend_class = self._backend_class(self.backend, source)
        common: dict[str, Any] = dict(
            ip=options.get("jlink_ip") or cfg.get("ip"),
            serial=options.get("jlink_serial") or cfg.get("serial"),
            timeout_s=float(cfg.get("timeout_s", 30)),
            log_path=self.harness.platform.run_dir / "jlink.log",
        )
        if backend_class is JLinkCommander:
            exe = cfg.get("executable") or os.environ.get("PASTEL_JLINKEXE", "JLinkExe")
            self._jl = JLinkCommander(exe, **common)
        else:
            self._jl = JLinkPylink(cfg.get("library"), **common)
        with self._lock:
            self._jl.start()
            minimum = float(cfg.get("min_vtref", 1.0))
            if minimum:
                vtref = self._jl.vtref()
                if vtref < minimum:
                    raise TargetError(
                        f"J-Link: target reference voltage {vtref} V is below {minimum} V: "
                        "is the board powered and the probe connected?"
                    )
            self._jl.connect(
                cfg["device"],
                cfg.get("interface", "JTAG"),
                int(cfg.get("speed", 4000)),
                cfg.get("jtag_conf", "-1 -1"),
            )
        log.info("J-Link connected (%s, through %s)", cfg["device"], self.backend)

    def close(self) -> None:  # noqa: D102
        with self._lock:
            if self._jl is not None:
                self._jl.close()
                self._jl = None

    # -- OnChipDebug --------------------------------------------------------------------

    def reset(self, *, halt: bool = True) -> None:  # noqa: D102
        with self._lock:
            jl = self._backend()
            if self.config.get("reset_pin"):
                jl.pulse_reset_pin()  # as APE did, before the debug reset
            jl.reset()
            if not halt:
                jl.go()
            elif not jl.is_halted():
                raise TargetError("target did not halt after reset")

    def halt(self) -> None:  # noqa: D102
        with self._lock:
            jl = self._backend()
            jl.halt()
            if not jl.is_halted():
                raise TargetError("target did not halt")

    def _resume(self, pc: int | None) -> None:
        with self._lock:
            jl = self._backend()
            if pc is not None:
                self._halted_for("setting the PC").write_register("pc", pc)
            jl.go()

    def _step(self) -> None:
        with self._lock:
            self._backend().step()

    def state(self) -> TargetState:  # noqa: D102
        with self._lock:
            halted = self._backend().is_halted()
        return TargetState.HALTED if halted else TargetState.RUNNING

    def _halted_for(self, what: str) -> JLinkBackend:
        jl = self._backend()
        if not jl.is_halted():
            # RISC-V debug reaches registers only while halted, and the library
            # doesn't report the failure: it hands back a value regardless.
            raise TargetError(f"J-Link: halt the CPU before {what}")
        return jl

    def read_register(self, name: str) -> int:  # noqa: D102
        with self._lock:
            return self._halted_for(f"reading {name}").read_register(name)

    def write_register(self, name: str, value: int) -> None:  # noqa: D102
        with self._lock:
            self._halted_for(f"writing {name}").write_register(name, value)

    @property
    def memory(self) -> MemoryInterface:  # noqa: D102
        return self._memory

    def _add_breakpoint(self, addr: int, *, hardware: bool) -> int:
        with self._lock:
            self._bp_handles[addr] = self._backend().set_breakpoint(addr, hardware)
        return addr

    def _remove_breakpoint(self, breakpoint_id: int) -> None:
        with self._lock:
            handle = self._bp_handles.pop(breakpoint_id, None)
            if handle is None:
                raise TargetError(f"no breakpoint at {breakpoint_id:#x}")
            self._backend().clear_breakpoint(handle)
