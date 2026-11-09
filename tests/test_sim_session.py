"""The host endpoint session against a fake simulator speaking the wire protocol."""

import socket
import struct
import threading

import pytest

from soc_pastel.core import CodeLocation, MemoryRegion, ProtocolError, Variant
from soc_pastel.core.endpoints import (
    CONTROL_ENDPOINT,
    EndpointInfo,
    Frame,
    FrameKind,
    Hello,
    decode_frames,
    encode_frame,
)
from soc_pastel.sim.handlers import Backdoor, BackdoorMemory, PlatformControl
from soc_pastel.sim.session import Listener, SocketEndpointSession

HANDLERS = {"pastel.platform": PlatformControl, "pastel.backdoor": Backdoor}


class FakeSim(threading.Thread):
    """Dials the listener, announces a RAM backdoor and the platform endpoint."""

    def __init__(self, spec, token=None, size=0x1000):
        super().__init__(daemon=True)
        host, port, tok = spec.split(":")
        self.addr, self.token = (host, int(port)), token or tok
        self.mem = bytearray(size)
        self.finished = threading.Event()

    def run(self):
        s = socket.create_connection(self.addr)
        eps = (
            EndpointInfo(0, "ram", "pastel.backdoor", "tb.ram"),
            EndpointInfo(1, "platform", "pastel.platform", "tb"),
        )
        s.sendall(
            encode_frame(
                Frame(
                    CONTROL_ENDPOINT,
                    FrameKind.HELLO,
                    Hello(1, self.token, eps, {"jtag_port": 1234}).encode(),
                )
            )
        )
        buf = bytearray()
        while True:
            data = s.recv(65536)
            if not data:
                return
            buf += data
            for f in decode_frames(buf):
                if f.kind is FrameKind.BYE:
                    return
                if f.kind is FrameKind.SYNC:
                    s.sendall(encode_frame(Frame(f.endpoint, FrameKind.SYNC_ACK, f.payload)))
                elif f.endpoint == 0:
                    op, seq, off, n = struct.unpack_from("<BIII", f.payload)
                    ok = off + n <= len(self.mem)
                    reply = bytes([op | 0x80]) + struct.pack("<I", seq) + bytes([0 if ok else 1])
                    if ok and op == 1:
                        reply += self.mem[off : off + n]
                    elif ok and op == 2:
                        self.mem[off : off + n] = f.payload[13 : 13 + n]
                    s.sendall(encode_frame(Frame(0, FrameKind.DATA, reply)))
                elif f.endpoint == 1:
                    if f.payload[0] == 0x01:
                        self.finished.set()
                    elif f.payload[0] == 0x02:
                        s.sendall(
                            encode_frame(
                                Frame(1, FrameKind.DATA, b"\x82" + (123456).to_bytes(8, "little"))
                            )
                        )


def connect(**kw):
    lst = Listener()
    sim = FakeSim(lst.spec, **kw)
    sim.start()
    conn = lst.accept(5)
    return lst, sim, conn


def test_session_backdoor_time_sync_and_finish():
    lst, sim, conn = connect()
    session = SocketEndpointSession(conn, lst.token, {}, HANDLERS)
    variant = Variant(
        name="v",
        regions=(MemoryRegion(name="ram", base=0x8000, size=0x1000, backdoor="ram"),),
        code_locations=(CodeLocation(name="ram", text="ram", data="ram"),),
    )
    mem = BackdoorMemory(variant, {"ram": session.handler("ram")})
    mem.write(0x8010, b"hello")
    assert sim.mem[0x10:0x15] == b"hello"
    assert mem.read(0x800E, 9) == b"\0\0hello\0\0"
    assert mem.can_access(0x8000, 0x1000) and not mem.can_access(0x9000)
    control = session.handler("platform")
    assert control.time_ns() == 123456
    session.sync("ram")
    control.request_finish()
    assert sim.finished.wait(5)
    assert session.hello.info["jtag_port"] == 1234
    session.close()
    lst.close()


def test_wrong_token_rejected():
    lst, _, conn = connect(token="nope")
    with pytest.raises(ProtocolError, match="token"):
        SocketEndpointSession(conn, lst.token, {}, HANDLERS)
    lst.close()
