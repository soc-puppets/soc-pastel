"""A stand-in for pylink (pylink-square), for testing the jlink driver's pylink backend.

``make_module()`` returns a fresh module to put in ``sys.modules["pylink"]``.
Like the fake J-Link Commander, it has a fake CPU and memory, and reads
FAKE_JLINK_VTREF (volts) and FAKE_JLINK_NO_PROBE. FAKE_PYLINK_HANG makes
``open()`` block, as the real library does with no probe attached, and
FAKE_PYLINK_NO_LIBRARY makes loading the library fail.
"""

import enum
import os
import threading
import types

RESET_VECTOR = 0x8000_0000
#: RISC-V register names as the J-Link library gives them.
REGISTER_NAMES = ["PC"] + [
    f"X{i} ({abi.upper()})"
    for i, abi in enumerate(
        "zero ra sp gp tp t0 t1 t2 s0 s1 a0 a1 a2 a3 a4 a5 a6 a7 "
        "s2 s3 s4 s5 s6 s7 s8 s9 s10 s11 t3 t4 t5 t6".split()
    )
]


def make_module() -> types.ModuleType:
    pylink = types.ModuleType("pylink")

    class JLinkException(Exception):
        pass

    class JLinkReadException(JLinkException):
        pass

    pylink.errors = types.SimpleNamespace(
        JLinkException=JLinkException, JLinkReadException=JLinkReadException
    )

    class JLinkInterfaces(enum.IntEnum):
        JTAG = 0
        SWD = 1

    pylink.enums = types.SimpleNamespace(JLinkInterfaces=JLinkInterfaces)

    class Library:
        def __init__(self, dllpath=None):
            if not os.path.exists(dllpath):
                raise OSError(f"{dllpath}: cannot open shared object file")
            self.path = dllpath

    pylink.library = types.SimpleNamespace(Library=Library)
    #: What the driver did, for tests to inspect.
    pylink.calls = []
    pylink.instances = []

    class JLink:
        def __init__(self, lib=None, log=None, detailed_log=None, error=None, warn=None):
            if os.environ.get("FAKE_PYLINK_NO_LIBRARY") and lib is None:
                raise TypeError("Expected to be given a valid DLL.")
            self.lib = lib
            self._log = log or (lambda s: None)
            self._opened = False
            self._connected = False
            self.mem: dict[int, int] = {}  # word address -> word
            self.regs = [0] * len(REGISTER_NAMES)
            self.halted_ = False
            self.bps: dict[int, int] = {}
            self.next_bp = 1
            self.reset_pin = []
            pylink.instances.append(self)

        def _record(self, *call):
            pylink.calls.append(call)

        def open(self, serial_no=None, ip_addr=None):
            self._record("open", serial_no, ip_addr)
            if os.environ.get("FAKE_PYLINK_HANG"):
                threading.Event().wait()  # forever, as the real one nearly does
            if os.environ.get("FAKE_JLINK_NO_PROBE"):
                raise JLinkException("No emulator found.")
            self._opened = True
            self._log('Device "J-Link" opened (fake)')

        def close(self):
            self._record("close")
            self._opened = False

        def set_tif(self, interface):
            self._record("set_tif", JLinkInterfaces(interface).name)

        def jtag_configure(self, instr_regs=0, data_bits=0):
            if instr_regs < 0 or data_bits < 0:
                raise ValueError("IR value is not a natural number.")
            self._record("jtag_configure", instr_regs, data_bits)

        def connect(self, chip_name, speed="auto", verbose=False):
            self._record("connect", chip_name, speed)
            if not self._opened:
                raise JLinkException("not open")
            self._connected = True
            self._log(f"Found {chip_name} core (fake)")

        @property
        def hardware_status(self):
            volts = float(os.environ.get("FAKE_JLINK_VTREF", "3.3"))
            return types.SimpleNamespace(voltage=int(volts * 1000))

        def halted(self):
            return self.halted_

        def halt(self):
            self.halted_ = True
            return True

        def restart(self, num_instructions=0, skip_breakpoints=False):
            if not self.halted_:
                return False
            self.halted_ = False
            return True

        def step(self, thumb=False):
            self.regs[0] += 4

        def reset(self, ms=0, halt=True):
            self.regs[0] = RESET_VECTOR
            self.halted_ = halt
            return 0

        def set_reset_pin_low(self):
            self.reset_pin.append(0)

        def set_reset_pin_high(self):
            self.reset_pin.append(1)

        def register_list(self):
            return list(range(len(REGISTER_NAMES)))

        def register_name(self, register_index):
            return REGISTER_NAMES[register_index]

        def register_read(self, register_index):
            return self.regs[register_index]

        def register_write(self, reg_index, value):
            self.regs[reg_index] = value
            return value

        def memory_read32(self, addr, num_words, zone=None):
            self._record("memory_read32", addr, num_words)
            if addr >= 0xF000_0000:
                raise JLinkReadException("Could not read memory.")
            return [self.mem.get(addr + 4 * i, 0) for i in range(num_words)]

        def memory_write32(self, addr, data, zone=None):
            self._record("memory_write32", addr, len(data))
            for i, word in enumerate(data):
                self.mem[addr + 4 * i] = word
            return len(data)

        def hardware_breakpoint_set(self, addr, thumb=False, arm=False):
            handle, self.next_bp = self.next_bp, self.next_bp + 1
            self.bps[handle] = addr
            return handle

        software_breakpoint_set = hardware_breakpoint_set

        def breakpoint_clear(self, handle):
            return self.bps.pop(handle, None) is not None

    pylink.JLink = JLink
    return pylink
