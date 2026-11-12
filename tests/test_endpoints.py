import pytest

from soc_pastel.core.endpoints import (
    CONTROL_ENDPOINT,
    EndpointInfo,
    Frame,
    FrameKind,
    Hello,
    decode_frames,
    encode_frame,
)
from soc_pastel.core.errors import ProtocolError


def test_frame_round_trip_and_partial_data():
    a = Frame(3, FrameKind.DATA, b"\x01\x02\x03")
    b = Frame(CONTROL_ENDPOINT, FrameKind.BYE)
    wire = encode_frame(a) + encode_frame(b)
    buf = bytearray(wire[:-2])
    assert decode_frames(buf) == [a]
    buf += wire[-2:]
    assert decode_frames(buf) == [b]
    assert buf == bytearray()


def test_frame_layout_is_little_endian():
    assert encode_frame(Frame(0x0102, FrameKind.DATA, b"x")) == bytes(
        [4, 0, 0, 0, 0x02, 0x01, 1, ord("x")]
    )


def test_bad_kind_rejected():
    with pytest.raises(ProtocolError):
        decode_frames(bytearray(b"\x03\x00\x00\x00\x00\x00\x09"))


def test_hello_round_trip_and_version_check():
    from soc_pastel.core.endpoints import PROTOCOL_VERSION

    eps = (EndpointInfo(0, "spi0", "spi", "tb.u_spi0"),)
    h = Hello(PROTOCOL_VERSION, "tok", eps, {"jtag_socket": "/tmp/x/jtag.sock"})
    assert Hello.decode(h.encode()) == h
    # A model built from older PASTEL HDL fails at once, and says why.
    with pytest.raises(ProtocolError, match="simulator speaks protocol v1, host speaks v2"):
        Hello.decode(Hello(1, "tok", ()).encode())


# --- Matching HELLO against the descriptor ------------------------------------------

from soc_pastel.core.endpoints import EndpointHandler, ExpectedEndpoint, match_hello  # noqa: E402
from soc_pastel.core.errors import ConfigurationError  # noqa: E402


class SpiHandler(EndpointHandler):
    type_name = "spi"

    def on_message(self, payload):
        pass


class BackdoorHandler(EndpointHandler):
    type_name = "pastel.backdoor"

    def on_message(self, payload):
        pass


def hello(*eps):
    from soc_pastel.core.endpoints import PROTOCOL_VERSION

    return Hello(
        PROTOCOL_VERSION,
        "t",
        tuple(EndpointInfo(i, n, t, f"tb.{n}") for i, (n, t) in enumerate(eps)),
    )


EXPECT = {"spi0": ExpectedEndpoint("spi", SpiHandler)}
PLATFORM = {"pastel.backdoor": BackdoorHandler}


def test_match_hello_assigns_handlers():
    chosen = match_hello(hello(("spi0", "spi"), ("backdoor", "pastel.backdoor")), EXPECT, PLATFORM)
    assert chosen == {"spi0": SpiHandler, "backdoor": BackdoorHandler}


def test_match_hello_reports_every_mismatch():
    with pytest.raises(ConfigurationError) as e:
        match_hello(hello(("spi1", "spi"), ("x", "pastel.unknown")), EXPECT, PLATFORM)
    msg = str(e.value)
    assert "'spi1'" in msg and "not bound" in msg
    assert "pastel.unknown" in msg
    assert "binds 'spi0'" in msg


def test_match_hello_type_mismatch():
    with pytest.raises(ConfigurationError, match="expects 'spi'"):
        match_hello(hello(("spi0", "uart")), EXPECT, PLATFORM)
