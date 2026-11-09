from soc_pastel.interfaces.on_chip_debug.openocd.driver import _OcdMemory


class FakeOcd:
    """Accepts only 32-bit aligned read_memory/write_memory, like VeeR's ICCM."""

    def __init__(self):
        self.mem = bytearray(64)

    def tcl(self, cmd):
        parts = cmd.split(maxsplit=3)
        addr, width = int(parts[1], 16), int(parts[2])
        assert width == 32 and addr % 4 == 0
        if parts[0] == "read_memory":
            n = int(parts[3])
            return " ".join(
                hex(int.from_bytes(self.mem[addr + 4 * i : addr + 4 * i + 4], "little"))
                for i in range(n)
            )
        for i, w in enumerate(parts[3].strip("{}").split()):
            self.mem[addr + 4 * i : addr + 4 * i + 4] = int(w, 16).to_bytes(4, "little")
        return ""


def test_unaligned_access_uses_words():
    ocd = FakeOcd()
    ocd.mem[:] = bytes(range(64))
    mem = _OcdMemory(ocd)
    mem.write(5, b"\xaa\xbb\xcc")
    assert ocd.mem[4:9] == bytes([4, 0xAA, 0xBB, 0xCC, 8])
    assert mem.read(3, 4) == bytes([3, 4, 0xAA, 0xBB])
