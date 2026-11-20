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
