"""Host side of the endpoint protocol over TCP."""

from __future__ import annotations

import logging
import secrets
import socket
import threading
from collections.abc import Mapping

from soc_pastel.core.endpoints import (
    CONTROL_ENDPOINT,
    EndpointHandler,
    EndpointSession,
    ExpectedEndpoint,
    Frame,
    FrameKind,
    Hello,
    decode_frames,
    encode_frame,
    match_hello,
)
from soc_pastel.core.errors import PastelTimeout, ProtocolError

log = logging.getLogger(__name__)


class Listener:
    """A listening socket the simulator dials back to.

    Attributes:
        token: Random token the simulator must present in HELLO.
        spec: Value for the ``+pastel_host`` plusarg.
    """

    def __init__(self, host: str = "127.0.0.1") -> None:
        self._sock = socket.create_server((host, 0))
        self.token = secrets.token_hex(8)
        port = self._sock.getsockname()[1]
        self.spec = f"{host}:{port}:{self.token}"

    def accept(self, timeout_s: float, alive: callable = lambda: True) -> socket.socket:
        """Wait for the simulator to connect, giving up if ``alive()`` turns false."""
        self._sock.settimeout(0.5)
        waited = 0.0
        while waited < timeout_s:
            try:
                conn, _ = self._sock.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                return conn
            except TimeoutError:
                waited += 0.5
                if not alive():
                    raise ProtocolError("simulator exited before connecting") from None
        raise PastelTimeout(f"simulator did not connect within {timeout_s:.0f} s")

    def close(self) -> None:
        """Stop listening."""
        self._sock.close()


class SocketEndpointSession(EndpointSession):
    """One simulator connection: HELLO, handler dispatch, SYNC and shutdown."""

    def __init__(
        self,
        conn: socket.socket,
        token: str,
        expected: Mapping[str, ExpectedEndpoint],
        platform_handlers: Mapping[str, type[EndpointHandler]],
        hello_timeout_s: float = 60.0,
    ) -> None:
        self._conn = conn
        self._send_lock = threading.Lock()
        self._buf = bytearray()
        self._closed = threading.Event()
        self._sync_cv = threading.Condition()
        self._sync_seq = 0
        self._acked: set[tuple[int, int]] = set()
        self.error: str | None = None

        conn.settimeout(hello_timeout_s)
        first = self._read_frames(block=True)
        if not first or first[0].kind is not FrameKind.HELLO:
            raise ProtocolError("simulator did not start with HELLO")
        self._hello = Hello.decode(first[0].payload)
        if self._hello.token != token:
            raise ProtocolError("simulator presented the wrong session token")
        classes = match_hello(self._hello, expected, platform_handlers)
        self._handlers: dict[str, EndpointHandler] = {}
        self._by_id: dict[int, EndpointHandler] = {}
        for info in self._hello.endpoints:
            h = classes[info.name](info, self._sender(info.id))
            self._handlers[info.name] = h
            self._by_id[info.id] = h
        conn.settimeout(None)
        for frame in first[1:]:
            self._dispatch(frame)
        self._reader = threading.Thread(
            target=self._read_loop, name="pastel-endpoints", daemon=True
        )
        self._reader.start()

    # -- EndpointSession ----------------------------------------------------------

    @property
    def hello(self) -> Hello:  # noqa: D102
        return self._hello

    def handler(self, name: str) -> EndpointHandler:  # noqa: D102
        try:
            return self._handlers[name]
        except KeyError:
            raise ProtocolError(f"no endpoint {name!r} in this simulation") from None

    def sync(self, name: str, timeout_s: float | None = 30.0) -> None:  # noqa: D102
        ep = self.handler(name).info.id
        with self._sync_cv:
            self._sync_seq += 1
            seq = self._sync_seq
        self._send(Frame(ep, FrameKind.SYNC, seq.to_bytes(4, "little")))
        with self._sync_cv:
            if not self._sync_cv.wait_for(
                lambda: (ep, seq) in self._acked or self._closed.is_set(), timeout_s
            ):
                raise PastelTimeout(f"endpoint {name!r} did not drain within {timeout_s} s")
            self._acked.discard((ep, seq))
        self._check_open()

    def close(self) -> None:  # noqa: D102
        if self._closed.is_set():
            return
        try:
            self._send(Frame(CONTROL_ENDPOINT, FrameKind.BYE))
        except OSError:
            pass
        self._closed.set()
        try:
            self._conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._conn.close()
        for h in self._handlers.values():
            h.on_close()

    @property
    def closed(self) -> bool:
        """True once the connection has ended."""
        return self._closed.is_set()

    # -- Internals ------------------------------------------------------------------

    def _sender(self, ep: int):
        return lambda payload: self._send(Frame(ep, FrameKind.DATA, bytes(payload)))

    def _send(self, frame: Frame) -> None:
        self._check_open()
        with self._send_lock:
            self._conn.sendall(encode_frame(frame))

    def _check_open(self) -> None:
        if self._closed.is_set():
            raise ProtocolError(self.error or "simulator connection closed")

    def _read_frames(self, block: bool) -> list[Frame]:
        while True:
            frames = decode_frames(self._buf)
            if frames or not block:
                return frames
            data = self._conn.recv(65536)
            if not data:
                raise ProtocolError("simulator closed the connection")
            self._buf += data

    def _read_loop(self) -> None:
        try:
            while not self._closed.is_set():
                data = self._conn.recv(65536)
                if not data:
                    break
                self._buf += data
                for frame in decode_frames(self._buf):
                    self._dispatch(frame)
        except (OSError, ProtocolError) as e:
            if not self._closed.is_set():
                self.error = f"simulator connection failed: {e}"
        finally:
            self._closed.set()
            with self._sync_cv:
                self._sync_cv.notify_all()
            for h in self._handlers.values():
                h.on_close()

    def _dispatch(self, frame: Frame) -> None:
        if frame.kind is FrameKind.DATA:
            handler = self._by_id.get(frame.endpoint)
            if handler is None:
                log.warning("DATA for unknown endpoint %d", frame.endpoint)
                return
            try:
                handler.on_message(frame.payload)
            except Exception:
                log.exception("handler for %s failed", handler.info.name)
        elif frame.kind is FrameKind.SYNC_ACK:
            with self._sync_cv:
                self._acked.add((frame.endpoint, int.from_bytes(frame.payload, "little")))
                self._sync_cv.notify_all()
        elif frame.kind is FrameKind.ERROR:
            self.error = frame.payload.decode(errors="replace")
            log.error("simulator reported: %s", self.error)
        elif frame.kind is FrameKind.BYE:
            self._closed.set()
