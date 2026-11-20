"""The firmware runtime: output, results, faults, checkpoints and data."""

import pytest

from soc_pastel.core.loader import elf_symbol, memory_for
from soc_pastel.fw.runner import FirmwareFailed, FirmwareFault


def test_hello(firmware):
    seen = []
    result = firmware.run("fw/hello.c", on_checkpoint=seen.append)
    assert result.passed
    assert f"Hello from {firmware.harness.device.name}" in result.output
    assert "signed -42, unsigned 42, hex 0x00c0ffee" in result.output
    assert "uint32_t 42, hex 0x00c0ffee" in result.output  # %lu, %lx
    assert seen == [1]
    assert result.data == [bytes.fromhex("deadbeef")]


def test_nonzero_result_is_reported(firmware):
    result = firmware.run("fw/fail.c", expect_pass=False)
    assert not result.passed
    assert result.result == 7
    with pytest.raises(FirmwareFailed, match="test_main returned 7"):
        firmware.run("fw/fail.c")


def test_checks_and_errors_carry_on_and_fail_the_test(firmware):
    result = firmware.run("fw/checks.c", expect_pass=False)
    assert result.result == 0 and not result.passed
    assert result.errors == [
        "checks.c:9: CHECK failed: three == 4",
        "checks.c:10: CHECK failed: three < 0: three is 3",
        "custom error beef",
    ]
    assert "checking 3 things\n" in result.output and "still running" in result.output
    with pytest.raises(FirmwareFailed, match="3 errors"):
        firmware.run("fw/checks.c")


def test_assert_ends_the_test(firmware):
    result = firmware.run("fw/checks.c", defines=["PASTEL_ASSERT_TEST"], expect_pass=False)
    assert result.result != 0
    assert result.errors[-1] == "checks.c:13: ASSERT failed: three == 5: stop here"
    assert "not reached" not in result.output and "still running" not in result.output


def test_fault_is_reported(firmware):
    with pytest.raises(FirmwareFault) as e:
        firmware.run("fw/fault.c")
    assert e.value.cause == 2  # illegal instruction


def test_expected_fault_is_returned(firmware):
    result = firmware.run("fw/fault.c", expect_fault=True)
    assert result.fault is not None and result.fault.cause == 2 and not result.passed
    with pytest.raises(FirmwareFailed, match="expected a fault"):
        firmware.run("fw/hello.c", expect_fault=True)


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


def test_host_sends_data_to_firmware(firmware):
    big = bytes((i * 7) & 0xFF for i in range(255))

    def on_checkpoint(cp):
        if cp == 1:  # the firmware is waiting: it keeps these until it asks
            firmware.send_u32(0xC0FFEE55)
            firmware.send(b"hello")
        elif cp == 2:  # it is already waiting in pastel_receive
            firmware.send(big)

    result = firmware.run("fw/receive.c", on_checkpoint=on_checkpoint)
    assert result.data == [bytes([sum(big) & 0xFF])]
