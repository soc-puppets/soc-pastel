import struct

from soc_pastel.core.memory import MemoryInterface
from soc_pastel.core.rtt import Rtt


class Ram(MemoryInterface):
    def __init__(self):
        self.m = bytearray(0x1000)

    def read(self, a, n):
        return bytes(self.m[a : a + n])

    def write(self, a, d):
        self.m[a : a + len(d)] = d


def make_block(ram, cb=0x100):
    ram.write(cb, b"SEGGER RTT\0\0\0\0\0\0" + struct.pack("<ii", 1, 1))
    ram.write(cb + 24, struct.pack("<6I", 0, 0x400, 8, 0, 0, 2))  # up 0: 8-byte buffer
    ram.write(cb + 48, struct.pack("<6I", 0, 0x500, 8, 0, 0, 2))  # down 0
    return cb


def test_up_channel_wraps():
    ram = Ram()
    cb = make_block(ram)
    rtt = Rtt(ram, cb)
    rtt.wait_ready(1)
    ram.write(0x400, b"ABCDEFGH")
    ram.write(cb + 24 + 12, struct.pack("<II", 2, 5))  # wr=2, rd=5: "FGH" + "AB"
    assert rtt.read_up(0) == b"FGHAB"
    assert rtt.read_up(0) == b""


def test_down_channel_respects_space():
    ram = Ram()
    cb = make_block(ram)
    rtt = Rtt(ram, cb)
    rtt.wait_ready(1)
    assert rtt.write_down(0, b"0123456789") == 7  # one slot always free
    assert ram.read(0x500, 7) == b"0123456"
