"""The firmware runtime: output, results, faults, checkpoints and data."""

import pytest

from soc_pastel.core.loader import elf_symbol, memory_for
from soc_pastel.fw.runner import FirmwareFault


def test_hello(firmware):
    seen = []
    result = firmware.run("fw/hello.c", on_checkpoint=seen.append)
    assert result.passed
    assert f"Hello from {firmware.harness.device.name}" in result.output
    assert "signed -42, unsigned 42, hex 0x00c0ffee" in result.output
    assert seen == [1]
    assert result.data == [bytes.fromhex("deadbeef")]


def test_nonzero_result_is_reported(firmware):
    result = firmware.run("fw/fail.c")
    assert not result.passed
    assert result.result == 7


def test_fault_is_reported(firmware):
    with pytest.raises(FirmwareFault) as e:
        firmware.run("fw/fault.c")
    assert e.value.cause == 2  # illegal instruction


def test_host_and_firmware_share_memory(firmware):
    elf = firmware.build("fw/memory.c")
    addr = elf_symbol(elf, "pastel_shared")
    harness = firmware.harness

    def on_checkpoint(cp):
        mem = memory_for(harness, addr, 64)
        # Some memories (VeeR's DCCM) are only reachable over debug while halted.
        with harness.ocd.halted():
            if cp == 1:
                mem.write_words(addr, [0x01010101 * i for i in range(16)])
            elif cp == 2:
                expected = [~(0x01010101 * i) & 0xFFFFFFFF for i in range(16)]
                assert mem.read_words(addr, 16) == expected

    from soc_pastel.fw.runner import run_firmware

    assert run_firmware(harness, elf, on_checkpoint=on_checkpoint).passed
