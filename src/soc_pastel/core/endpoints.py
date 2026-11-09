"""The endpoint protocol between the host and a running simulator.

Every VIP in the testbench (and the platform's own backdoor and control
endpoints) is an *endpoint*: a named byte-message channel. A small C pump
inside the simulator moves messages between DPI queues and one TCP
connection; all message interpretation happens on the host, in
:class:`EndpointHandler` subclasses supplied by interface packs. No Python
runs inside the simulator.

Connection
----------
The host listens; the simulator dials out to the address given in the
``+pastel_host=<ip>:<port>:<token>`` plusarg and sends :data:`FrameKind.HELLO`
on the control endpoint. The token identifies the test session, so one
listener can serve parallel simulations.

Wire format
-----------
All integers are little-endian. Each frame is::

    u32 length      number of bytes that follow this field (3 + len(payload))
    u16 endpoint    endpoint id; 0xFFFF is the control endpoint
    u8  kind        FrameKind
    u8  payload[length - 3]

HELLO payload (simulator -> host, control endpoint) is UTF-8 JSON::

    {"version": 1, "token": "...",
     "endpoints": [{"id": 0, "name": "spi0", "type": "spi", "path": "tb.u_spi0"}, ...],
     "info": {"openocd_tcl_port": 6666, ...}}

DATA payloads are opaque to the core; each endpoint type defines its own.
ERROR payloads are a UTF-8 message. BYE has an empty payload and ends the
session. SYNC (host -> simulator, payload ``u32 seq``) asks the pump to reply
with SYNC_ACK carrying the same ``seq`` once the VIP has consumed every
message sent to that endpoint before the SYNC.

Reserved endpoint types: ``"pastel.backdoor"`` (memory backdoor) and
``"pastel.platform"`` (finish, waves, time). Pack-defined types should use
their interface type name (``"spi"``) or a dotted vendor prefix.

Finding handlers
----------------
Handlers are not registered on their own. Each endpoint is either:

* an **interface endpoint** -- its name is an interface instance name in the
  device descriptor (``"spi0"``), and the bound implementation, an
  :class:`EndpointBacked` interface, names the handler class; or
* a **platform endpoint** -- its type starts with ``"pastel."`` and the
  simulation platform supplies the handler.

:func:`match_hello` checks HELLO against both, so a testbench and descriptor
that disagree fail clearly when the session starts.
"""

from __future__ import annotations

import json
import struct
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any, ClassVar

from .errors import ConfigurationError, ProtocolError

if TYPE_CHECKING:
    from .harness import PlatformHarness

#: Protocol version carried in HELLO. Bump on any incompatible change.
PROTOCOL_VERSION = 1

#: Endpoint id used for HELLO, BYE and session-level ERROR frames.
CONTROL_ENDPOINT = 0xFFFF

#: Prefix of endpoint types owned by the simulation platform itself.
PLATFORM_ENDPOINT_PREFIX = "pastel."

#: Largest accepted value of the length field.
MAX_FRAME_LENGTH = 16 * 1024 * 1024

_LEN = struct.Struct("<I")
_HDR = struct.Struct("<HB")


class FrameKind(IntEnum):
    """Kind of frame."""

    HELLO = 0
    DATA = 1
    ERROR = 2
    BYE = 3
    SYNC = 4
    SYNC_ACK = 5


@dataclass(frozen=True)
class Frame:
    """One protocol frame."""

    endpoint: int
    kind: FrameKind
    payload: bytes = b""


def encode_frame(frame: Frame) -> bytes:
    """Serialise a frame for the wire."""
    length = _HDR.size + len(frame.payload)
    if length > MAX_FRAME_LENGTH:
        raise ProtocolError(f"frame too long ({length} bytes)")
    return _LEN.pack(length) + _HDR.pack(frame.endpoint, frame.kind) + frame.payload


def decode_frames(buffer: bytearray) -> list[Frame]:
    """Remove and return every complete frame at the start of ``buffer``.

    Incomplete trailing data is left in ``buffer`` for the next call.

    Raises:
        ProtocolError: A frame header is malformed.
    """
    frames: list[Frame] = []
    while len(buffer) >= _LEN.size:
        (length,) = _LEN.unpack_from(buffer)
        if not _HDR.size <= length <= MAX_FRAME_LENGTH:
            raise ProtocolError(f"bad frame length {length}")
        if len(buffer) < _LEN.size + length:
            break
        endpoint, kind = _HDR.unpack_from(buffer, _LEN.size)
        try:
            kind = FrameKind(kind)
        except ValueError:
            raise ProtocolError(f"unknown frame kind {kind}") from None
        start = _LEN.size + _HDR.size
        frames.append(Frame(endpoint, kind, bytes(buffer[start : _LEN.size + length])))
        del buffer[: _LEN.size + length]
    return frames


@dataclass(frozen=True)
class EndpointInfo:
    """Description of one endpoint, as announced in HELLO."""

    id: int
    name: str
    type: str
    path: str


@dataclass(frozen=True)
class Hello:
    """Contents of the HELLO frame."""

    version: int
    token: str
    endpoints: tuple[EndpointInfo, ...]
    info: Mapping[str, Any] = field(default_factory=dict)

    def encode(self) -> bytes:
        """Serialise to a HELLO frame payload."""
        return json.dumps(
            {
                "version": self.version,
                "token": self.token,
                "endpoints": [vars(e) for e in self.endpoints],
                "info": dict(self.info),
            }
        ).encode()

    @classmethod
    def decode(cls, payload: bytes) -> Hello:
        """Parse a HELLO frame payload and check the protocol version."""
        try:
            raw = json.loads(payload.decode())
            hello = cls(
                version=int(raw["version"]),
                token=str(raw["token"]),
                endpoints=tuple(EndpointInfo(**e) for e in raw["endpoints"]),
                info=raw.get("info", {}),
            )
        except (ValueError, KeyError, TypeError) as e:
            raise ProtocolError(f"malformed HELLO: {e}") from None
        if hello.version != PROTOCOL_VERSION:
            raise ProtocolError(
                f"simulator speaks protocol v{hello.version}, host speaks v{PROTOCOL_VERSION}"
            )
        ids = [e.id for e in hello.endpoints]
        names = [e.name for e in hello.endpoints]
        if len(set(ids)) != len(ids) or len(set(names)) != len(names) or CONTROL_ENDPOINT in ids:
            raise ProtocolError("HELLO endpoint ids/names must be unique and not 0xFFFF")
        return hello


class EndpointHandler(ABC):
    """Host-side handler for one endpoint.

    Handlers are created by the endpoint session when HELLO announces an
    endpoint whose type has a registered handler. :meth:`on_message` is called
    on the session's reader thread; handlers that share state with test code
    must protect it.

    Args:
        info: The endpoint as announced by the simulator.
        send: Callable that sends a DATA payload to this endpoint.
    """

    #: Endpoint type this handler serves, as announced in HELLO.
    type_name: ClassVar[str]

    def __init__(self, info: EndpointInfo, send: Callable[[bytes], None]) -> None:
        self.info = info
        self._send = send

    def send(self, payload: bytes) -> None:
        """Send a DATA payload to the VIP."""
        self._send(payload)

    @abstractmethod
    def on_message(self, payload: bytes) -> None:
        """Handle a DATA payload from the VIP."""

    def on_close(self) -> None:  # noqa: B027 - optional hook
        """Called once when the session ends."""


class EndpointSession(ABC):
    """The host side of one simulator connection."""

    @property
    @abstractmethod
    def hello(self) -> Hello:
        """The HELLO the simulator sent."""

    @abstractmethod
    def handler(self, name: str) -> EndpointHandler:
        """Return the handler for the endpoint called ``name``."""

    @abstractmethod
    def sync(self, name: str, timeout_s: float | None = None) -> None:
        """Block until the VIP has consumed every message sent to ``name`` so far."""

    @abstractmethod
    def close(self) -> None:
        """Send BYE and close the connection. Idempotent."""


class EndpointBacked:
    """Mixin for interface implementations backed by a testbench VIP.

    Combine with an interface contract, e.g.
    ``class SimSpi(EndpointBacked, SpiEndpoint)``. The VIP must announce an
    endpoint whose name is the interface instance name and whose type is
    :attr:`endpoint_type`.
    """

    #: Host-side handler class for this implementation's VIP.
    handler_class: ClassVar[type[EndpointHandler]]

    #: Endpoint type the VIP announces in HELLO; defaults to ``type_name``.
    endpoint_type: ClassVar[str | None] = None

    # Provided by Interface.
    name: str
    harness: PlatformHarness
    type_name: ClassVar[str]

    @classmethod
    def announced_type(cls) -> str:
        """The endpoint type this implementation expects in HELLO."""
        return cls.endpoint_type or cls.type_name

    @property
    def endpoint(self) -> EndpointHandler:
        """This interface's handler in the live endpoint session."""
        from .platform import SimulationPlatform

        platform = self.harness.platform
        if not isinstance(platform, SimulationPlatform):
            raise ConfigurationError(f"{self.name!r} is endpoint-backed but not in a simulation")
        return platform.endpoints.handler(self.name)


@dataclass(frozen=True)
class ExpectedEndpoint:
    """An endpoint the host expects the testbench to announce."""

    type: str
    handler_class: type[EndpointHandler]


def match_hello(
    hello: Hello,
    interfaces: Mapping[str, ExpectedEndpoint],
    platform_handlers: Mapping[str, type[EndpointHandler]],
) -> dict[str, type[EndpointHandler]]:
    """Choose a handler class for every endpoint in HELLO, checking for mismatches.

    Args:
        hello: What the simulator announced.
        interfaces: Endpoint-backed interfaces bound by the device descriptor,
            keyed by instance name.
        platform_handlers: Handlers the platform provides, keyed by endpoint
            type (each starting with ``"pastel."``).

    Returns:
        Handler class for each announced endpoint, keyed by endpoint name.

    Raises:
        ConfigurationError: Listing every mismatch between testbench and
            descriptor: missing endpoints, unexpected endpoints, wrong types,
            and platform endpoint types the platform cannot handle.
    """
    problems: list[str] = []
    chosen: dict[str, type[EndpointHandler]] = {}
    for ep in hello.endpoints:
        if ep.type.startswith(PLATFORM_ENDPOINT_PREFIX):
            if ep.type in platform_handlers:
                chosen[ep.name] = platform_handlers[ep.type]
            else:
                problems.append(f"{ep.name!r} has platform type {ep.type!r}, which is unsupported")
        elif ep.name in interfaces:
            want = interfaces[ep.name]
            if ep.type != want.type:
                problems.append(
                    f"{ep.name!r} is type {ep.type!r}; descriptor expects {want.type!r}"
                )
            else:
                chosen[ep.name] = want.handler_class
        else:
            problems.append(
                f"testbench endpoint {ep.name!r} ({ep.path}) is not bound in the descriptor"
            )
    announced = {ep.name for ep in hello.endpoints}
    for name in sorted(set(interfaces) - announced):
        problems.append(f"descriptor binds {name!r} but the testbench has no such endpoint")
    if problems:
        raise ConfigurationError("testbench and descriptor disagree:\n  " + "\n  ".join(problems))
    return chosen
