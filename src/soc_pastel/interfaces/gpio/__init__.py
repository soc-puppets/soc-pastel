"""GPIO interface contract.

The far end of the chip's GPIO pins: something that can observe what the chip
drives and drive the pins the chip reads. Implementations: a simulation VIP,
a bench I/O adapter, or a board loopback.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from soc_pastel.core.interface import Interface


class Gpio(Interface):
    """Observe and drive the chip's GPIO pins from outside the chip.

    Pin values are integers with bit ``n`` for pin ``n``.
    """

    type_name: ClassVar[str] = "gpio"

    @property
    @abstractmethod
    def width(self) -> int:
        """Number of pins."""

    @abstractmethod
    def sample(self) -> int:
        """Return the current level of every pin as seen at the chip boundary."""

    @abstractmethod
    def drive(self, value: int, mask: int) -> None:
        """Drive the pins selected by ``mask`` to the corresponding bits of ``value``."""

    @abstractmethod
    def release(self, mask: int) -> None:
        """Stop driving the pins selected by ``mask``."""
