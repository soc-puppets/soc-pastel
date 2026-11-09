"""SPI interface contract.

The far end of a chip's SPI controller: a device the chip talks to. The
contract is transaction-level and load-then-capture, because neither the
simulator nor a USB adapter can let the host answer byte by byte: responses
are loaded before the chip starts a transfer, and what the chip sent is
captured for inspection afterwards. Reactive devices (a flash model, say)
run beside the hardware, in the VIP or the adapter, and are configured from
the host.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from soc_pastel.core.interface import Interface


@dataclass(frozen=True)
class SpiTransfer:
    """One chip-select frame.

    Attributes:
        cs: Chip-select index.
        mosi: Bytes the chip sent.
        miso: Bytes returned to the chip.
    """

    cs: int
    mosi: bytes
    miso: bytes


class SpiEndpoint(Interface):
    """Act as the device on the far end of the chip's SPI bus."""

    type_name: ClassVar[str] = "spi"

    @abstractmethod
    def configure(self, *, mode: int = 0, bits: int = 8, cs: int = 0) -> None:
        """Set SPI mode (0-3), word size and which chip-select to respond to."""

    @abstractmethod
    def load_response(self, data: bytes) -> None:
        """Queue bytes to return on MISO in subsequent transfers."""

    @abstractmethod
    def captured(self) -> list[SpiTransfer]:
        """Return every completed transfer since the last :meth:`clear`."""

    @abstractmethod
    def clear(self) -> None:
        """Discard captured transfers and any unsent responses."""
