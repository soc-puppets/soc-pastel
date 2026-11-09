"""UART interface contract.

The far end of a chip's UART: a terminal that records what the chip sends and
can send data to the chip.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from soc_pastel.core.interface import Interface


class Uart(Interface):
    """Act as the terminal on the far end of the chip's UART."""

    type_name: ClassVar[str] = "uart"

    @abstractmethod
    def configure(
        self, *, baud: int, data_bits: int = 8, parity: str = "N", stop_bits: int = 1
    ) -> None:
        """Set the line format. Must match what the firmware configures."""

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Send ``data`` to the chip."""

    @abstractmethod
    def received(self) -> bytes:
        """Return everything received since the last :meth:`clear`, without consuming it."""

    @abstractmethod
    def wait_for(self, pattern: bytes, timeout_s: float) -> bytes:
        """Block until ``pattern`` has been received; return everything up to and including it.

        Raises:
            PastelTimeout: ``pattern`` did not arrive in time.
        """

    @abstractmethod
    def clear(self) -> None:
        """Discard received data."""
