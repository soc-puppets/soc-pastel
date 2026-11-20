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
