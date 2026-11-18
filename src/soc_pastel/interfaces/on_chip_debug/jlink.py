"""On-chip debug through a SEGGER J-Link, by driving J-Link Commander (``JLinkExe``).

Ported from APE's ``jlink_exe_wrapper``: it runs ``JLinkExe`` under
:mod:`pexpect`, sends Commander commands and parses the replies. It needs
SEGGER's J-Link software (``JLinkExe``) and ``pexpect``
(``pip install "soc-pastel[jlink]"``).

Bind it for a hardware platform::

    "on_chip_debug": InterfaceBinding(type="on_chip_debug", impls={
        "fpga": {"impl": "jlink", "device": "RISC-V", "interface": "JTAG", "speed": 4000},
    })

Binding config keys:

- ``impl``: ``"jlink"``.
- ``device``: the J-Link device name (``device`` command), e.g. ``"RISC-V"``.
- ``interface``: ``"JTAG"`` (default) or ``"SWD"``.
- ``speed``: interface speed in kHz (default 4000).
- ``ip``: connect to a J-Link over IP or a SEGGER tunnel (``-IP``), e.g.
  ``"tunnel:<name>"``. ``serial``: pick a USB J-Link by serial number. Neither:
  the only USB J-Link. Both can be given at run time with ``--jlink-ip`` and
  ``--jlink-serial``.
- ``jtag_conf``: ``JTAGConf`` arguments (default ``"-1 -1"``: auto-detect the chain).
- ``reset_pin``: also pulse the nRESET pin when resetting (default False).
- ``min_vtref``: fail to connect if the target's reference voltage is lower
  (default 1.0 V; ``0`` skips the check).
- ``executable``: J-Link Commander (default ``$PASTEL_JLINKEXE`` or ``JLinkExe``).
- ``timeout_s``: how long one command may take (default 30).
"""

from __future__ import annotations

import logging
import os
import re
import struct
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from soc_pastel.core.errors import ConfigurationError, PastelTimeout, TargetError
from soc_pastel.core.memory import MemoryInterface, WordMemory
from soc_pastel.core.on_chip_debug import OnChipDebug, TargetState

if TYPE_CHECKING:
    from soc_pastel.core.harness import PlatformHarness

log = logging.getLogger(__name__)

_KEYS = {
    "impl", "device", "interface", "speed", "ip", "serial", "jtag_conf", "reset_pin",
    "min_vtref", "executable", "timeout_s",
}  # fmt: skip
#: Commander's error messages, e.g. ``****** Error: ...``, ``ERROR: ...`` or
#: ``Connecting to J-Link via USB...FAILED: Cannot connect to J-Link.``
_ERROR = re.compile(r"^\s*(\*+\s*Error\b|ERROR\b|Could not)|\bFAILED:", re.I | re.M)


class JLinkCommander:
    """J-Link Commander (``JLinkExe``), driven over pexpect. From APE's wrapper.

    Args:
        executable: ``JLinkExe``.
        ip: ``-IP`` value (IP address or ``tunnel:<name>``), or None.
        serial: USB serial number (``-SelectEmuBySN``), or None.
        timeout_s: How long one command may take.
        log_path: Where to keep everything Commander printed.
    """

    PROMPT = "J-Link>"

    def __init__(
        self,
        executable: str = "JLinkExe",
        *,
        ip: str | None = None,
        serial: str | None = None,
        timeout_s: float = 30.0,
        log_path: Path | None = None,
    ) -> None:
        self.executable = executable
        self.ip = ip
        self.serial = serial
        self.timeout_s = timeout_s
        self.log_path = log_path
        self._proc: Any = None
        self._log_file: Any = None

    def start(self) -> None:
        """Start Commander and wait for its prompt."""
        try:
            import pexpect
        except ImportError:
            raise ConfigurationError(
                'the jlink driver needs pexpect: pip install "soc-pastel[jlink]"'
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
            where = (
                f"at {self.ip}"
                if self.ip
                else f"with serial {self.serial}"
                if self.serial
                else "on USB"
            )
            raise TargetError(
                f"J-Link Commander can't reach a J-Link {where} (pick one with --jlink-serial "
                f"or --jlink-ip):\n{banner.strip()}"
            )

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

    # -- Commands (as APE's wrapper) ------------------------------------------------------

    def connect(self, device: str, interface: str, speed: int, jtag_conf: str) -> None:
        """Select the device and interface, and connect to the target."""
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

    def read_register(self, name: str) -> int:
        """Read a register (``rreg``)."""
        out = self.command(f"rreg {name}")
        match = re.search(rf"\b{re.escape(name)}\s*=\s*(?:0x)?([0-9A-Fa-f]+)", out, re.I)
        if not match:
            raise TargetError(f"J-Link: cannot read register {name} from:\n{out.strip()}")
        return int(match.group(1), 16)

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


class _JLinkMemory(WordMemory):
    """Target memory through J-Link Commander, in aligned 32-bit words."""

    #: Above this many words, go through a file (savebin/loadbin) instead.
    BULK_WORDS = 64

    def __init__(self, jlink: JLink) -> None:
        self._jlink = jlink

    def _read_words(self, addr: int, count: int) -> bytes:
        with self._jlink._lock:
            jl = self._jlink._commander()
            if count > self.BULK_WORDS:
                return jl.savebin(addr, 4 * count)
            return struct.pack(f"<{count}I", *jl.mem32(addr, count))

    def _write_words(self, addr: int, data: bytes) -> None:
        with self._jlink._lock:
            jl = self._jlink._commander()
            if len(data) > 4 * self.BULK_WORDS:
                jl.loadbin(addr, data)
            else:
                jl.write32(addr, list(struct.unpack(f"<{len(data) // 4}I", data)))


class JLink(OnChipDebug):
    """On-chip debug through a SEGGER J-Link, driving J-Link Commander.

    For hardware platforms (FPGA boards, silicon). Commander's output goes to
    ``jlink.log`` in the test's run directory. Commands are serialised, so the
    firmware runner can poll memory while a test calls in from a callback.
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
        self._lock = threading.RLock()
        self._jl: JLinkCommander | None = None
        self._memory = _JLinkMemory(self)
        self._breakpoints: dict[int, int] = {}  # address -> Commander's handle

    def _commander(self) -> JLinkCommander:
        if self._jl is None:
            raise TargetError("the J-Link is not connected")
        return self._jl

    def _cmd(self, cmd: str, **kwargs: Any) -> str:
        with self._lock:
            return self._commander().command(cmd, **kwargs)

    # -- Lifecycle ----------------------------------------------------------------------

    def open(self) -> None:  # noqa: D102
        options = self.harness.platform.options
        cfg = self.config
        self._jl = JLinkCommander(
            cfg.get("executable") or os.environ.get("PASTEL_JLINKEXE", "JLinkExe"),
            ip=options.get("jlink_ip") or cfg.get("ip"),
            serial=options.get("jlink_serial") or cfg.get("serial"),
            timeout_s=float(cfg.get("timeout_s", 30)),
            log_path=self.harness.platform.run_dir / "jlink.log",
        )
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
        log.info("J-Link connected (%s)", cfg["device"])

    def close(self) -> None:  # noqa: D102
        with self._lock:
            if self._jl is not None:
                self._jl.close()
                self._jl = None

    # -- OnChipDebug --------------------------------------------------------------------

    def reset(self, *, halt: bool = True) -> None:  # noqa: D102
        with self._lock:
            if self.config.get("reset_pin"):
                self._cmd("clrreset")  # nRESET low ...
                self._cmd("setreset")  # ... and high again (as APE did)
            self._cmd("r")  # Commander's reset halts the CPU
            if not halt:
                self._cmd("g")
            elif not self._commander().is_halted():
                raise TargetError("target did not halt after reset")

    def halt(self) -> None:  # noqa: D102
        with self._lock:
            self._cmd("h")
            if not self._commander().is_halted():
                raise TargetError("target did not halt")

    def resume(self, pc: int | None = None) -> None:  # noqa: D102
        with self._lock:
            if pc is not None:
                self._cmd(f"setpc 0x{pc:X}")
            self._cmd("g")

    def step(self) -> None:  # noqa: D102
        self._cmd("s")

    def state(self) -> TargetState:  # noqa: D102
        with self._lock:
            halted = self._commander().is_halted()
        return TargetState.HALTED if halted else TargetState.RUNNING

    def read_register(self, name: str) -> int:  # noqa: D102
        with self._lock:
            return self._commander().read_register(name)

    def write_register(self, name: str, value: int) -> None:  # noqa: D102
        self._cmd(f"wreg {name} 0x{value:X}")

    @property
    def memory(self) -> MemoryInterface:  # noqa: D102
        return self._memory

    def add_breakpoint(self, addr: int, *, hardware: bool = True) -> int:  # noqa: D102
        out = self._cmd(f"setbp 0x{addr:X}" + (" H" if hardware else " S"))
        match = re.search(r"handle\s*=?\s*(?:0x)?([0-9A-Fa-f]+)", out, re.I)
        self._breakpoints[addr] = int(match.group(1), 16) if match else -1
        return addr

    def remove_breakpoint(self, breakpoint_id: int) -> None:  # noqa: D102
        handle = self._breakpoints.pop(breakpoint_id, None)
        if handle is None:
            raise TargetError(f"no breakpoint at {breakpoint_id:#x}")
        self._cmd(f"clrbp {handle:X}" if handle >= 0 else "clrbp")
