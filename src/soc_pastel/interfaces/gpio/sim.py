"""GPIO in simulation: the host side of the ``pastel_gpio`` testbench agent.

Bind it in the device descriptor for simulation variants::

    "gpio": InterfaceBinding(type="gpio", impls={"sim": {"impl": "sim"}})

and instantiate ``pastel_gpio`` in the testbench with ``NAME`` set to the
binding's instance name (here ``"gpio"``). See ``hw/pastel_gpio.sv`` for the
wiring and the endpoint protocol.
"""

from __future__ import annotations

import struct
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from soc_pastel.core.endpoints import EndpointBacked
from soc_pastel.core.errors import ConfigurationError
from soc_pastel.sim.handlers import RequestReplyHandler

from . import Gpio

if TYPE_CHECKING:
    from soc_pastel.core.harness import PlatformHarness


class GpioAgent(RequestReplyHandler):
    """Talks to one ``pastel_gpio`` agent: endpoint type ``gpio``."""

    type_name = "gpio"
    SAMPLE, DRIVE, RELEASE = 1, 2, 3
    _REPLY = struct.Struct("<BIBQQ")  # op|0x80, seq, width, value, unknown

    def on_message(self, payload: bytes) -> None:  # noqa: D102
        if len(payload) == self._REPLY.size:
            _, seq, *_ = self._REPLY.unpack(payload)
            self._deliver(seq, payload)

    def request(self, op: int, *fields: int) -> tuple[int, int, int]:
        """Send one request; return ``(width, value, unknown)`` from the reply."""
        seq = self._next_seq()
        self.send(struct.pack(f"<BI{len(fields)}Q", op, seq, *fields))
        what = {self.SAMPLE: "sample", self.DRIVE: "drive", self.RELEASE: "release"}[op]
        _, _, width, value, unknown = self._REPLY.unpack(self._wait(seq, f"GPIO {what}"))
        return width, value, unknown


class SimGpio(EndpointBacked, Gpio):
    """The chip's GPIO pins in simulation, through the ``pastel_gpio`` agent.

    :meth:`drive` and :meth:`release` return once the change has reached the
    pins, so the chip sees it before anything the test does next (a register
    read over debug, or a checkpoint acknowledgement to firmware).

    An X or Z pin samples as 0; :meth:`sample_unknown` says which pins those
    are, e.g. to check that nothing drives a pin. A 2-state simulator
    (Verilator) has no X or Z: an undriven pin is 0 there, and contention
    between the agent and the chip is invisible.
    """

    impl_name: ClassVar[str] = "sim"
    handler_class = GpioAgent

    def __init__(self, name: str, config: Mapping[str, Any], harness: PlatformHarness) -> None:
        super().__init__(name, config, harness)
        unknown = set(config) - {"impl"}
        if unknown:
            raise ConfigurationError(f"{name}: unknown gpio/sim config keys {sorted(unknown)}")
        self._width: int | None = None
        self._unknown = 0
        self._lock = threading.Lock()

    def _request(self, op: int, *fields: int) -> int:
        with self._lock:
            width, value, unknown = self.endpoint.request(op, *fields)  # type: ignore[attr-defined]
            self._width, self._unknown = width, unknown
            return value

    def _check_mask(self, mask: int) -> int:
        if mask < 0 or mask >> self.width:
            raise ValueError(f"{self.name}: mask {mask:#x} has pins beyond its {self.width}")
        return mask

    @property
    def width(self) -> int:
        """Number of pins, as the testbench agent declares it."""
        if self._width is None:
            self._request(GpioAgent.SAMPLE)
        return self._width  # type: ignore[return-value]

    def sample(self) -> int:  # noqa: D102
        return self._request(GpioAgent.SAMPLE)

    def sample_unknown(self) -> int:
        """Sample the pins; return a mask of those that are X or Z (always 0 in Verilator)."""
        self._request(GpioAgent.SAMPLE)
        return self._unknown

    def drive(self, value: int, mask: int) -> None:  # noqa: D102
        mask = self._check_mask(mask)
        self._request(GpioAgent.DRIVE, value & mask, mask)

    def release(self, mask: int) -> None:  # noqa: D102
        self._request(GpioAgent.RELEASE, self._check_mask(mask))
