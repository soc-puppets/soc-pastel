"""HostLink: sending data to running firmware."""

import pytest

from soc_pastel.core import TargetError
from soc_pastel.fw.runner import HostLink, Mbox


def test_send_only_while_firmware_runs():
    link = HostLink()
    with pytest.raises(TargetError, match="no firmware is running"):
        link.send(b"x")


def test_messages_and_their_limit():
    link = HostLink()
    sent = []
    link._send = lambda mid, payload: sent.append((mid, payload))
    link.send(b"hi")
    link.send_u32(0x1_2345_6789)  # masked to 32 bits, little-endian
    assert sent == [(Mbox.HOST_DATA, b"hi"), (Mbox.HOST_DATA, bytes.fromhex("89674523"))]
    with pytest.raises(ValueError, match="at most 255 bytes"):
        link.send(bytes(256))


def test_waiting_while_the_firmware_is_held_warns(caplog):
    import struct
    import threading
    from pathlib import Path
    from types import SimpleNamespace

    from soc_pastel.fw.runner import FirmwareRun

    harness = SimpleNamespace(timeout=SimpleNamespace(check=lambda: None))
    run = FirmwareRun(harness, Path("held.elf"))
    run.HELD_WARNING_S = 0.1
    run._held = [struct.pack("<I", 1)]  # held at waiting checkpoint 1

    def end_it():  # as if the firmware's thread had stopped with an error
        with run._cv:
            run._error = RuntimeError("stopped")
            run._cv.notify_all()

    threading.Timer(0.5, end_it).start()
    with pytest.raises(RuntimeError, match="stopped"):
        run.wait_checkpoint(2)
    assert "held at waiting checkpoint 1" in caplog.text


def test_a_previous_runs_rtt_block_is_invalidated_before_the_firmware_starts(monkeypatch):
    """Two runs in one test: the new firmware's start-up code runs before it sets up
    RTT, and meanwhile the old control block (in .bss, which loading leaves alone)
    must not look valid, or the host reads the old run's buffers."""
    from pathlib import Path
    from types import SimpleNamespace

    from soc_pastel.fw import runner

    events = []
    rtt_id = bytearray(b"SEGGER RTT\0\0\0\0\0\0")  # left by the previous run

    class Memory:
        def read(self, addr, length):
            return bytes(rtt_id[:length]) + bytes(max(0, length - 16))

        def write(self, addr, data):
            events.append(("clear id", bytes(data)))
            rtt_id[:] = data[:16]

    class Ocd:
        def reset(self, *, halt):
            events.append("reset")

        def resume(self, pc=None):
            events.append("resume")
            rtt_id[:] = b"SEGGER RTT\0\0\0\0\0\0"  # the new firmware sets it up

    harness = SimpleNamespace(ocd=Ocd(), timeout=SimpleNamespace(check=lambda: None))
    monkeypatch.setattr(runner, "load_elf", lambda h, elf: 0x100)
    monkeypatch.setattr(runner, "elf_symbol", lambda elf, name: 0x2000)
    monkeypatch.setattr(runner, "memory_for", lambda h, addr, length: Memory())
    run = runner.FirmwareRun(harness, Path("fw.elf"))
    run._launch()
    assert events == ["reset", ("clear id", bytes(16)), "resume"]
