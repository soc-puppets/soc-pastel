"""PyOcd implementation of the on-chip debug contract (not yet implemented)."""

from __future__ import annotations

from typing import ClassVar

from soc_pastel.core.memory import MemoryInterface
from soc_pastel.core.on_chip_debug import OnChipDebug, TargetState

_TODO = "PyOcd on-chip debug is not implemented yet"


class PyOcd(OnChipDebug):
    """On-chip debug through pyOCD (``pip install "soc-pastel[pyocd]"``)."""

    impl_name: ClassVar[str] = "pyocd"

    def reset(self, *, halt: bool = True) -> None:  # noqa: D102
        raise NotImplementedError(_TODO)

    def halt(self) -> None:  # noqa: D102
        raise NotImplementedError(_TODO)

    def resume(self, pc: int | None = None) -> None:  # noqa: D102
        raise NotImplementedError(_TODO)

    def step(self) -> None:  # noqa: D102
        raise NotImplementedError(_TODO)

    def state(self) -> TargetState:  # noqa: D102
        raise NotImplementedError(_TODO)

    def read_register(self, name: str) -> int:  # noqa: D102
        raise NotImplementedError(_TODO)

    def write_register(self, name: str, value: int) -> None:  # noqa: D102
        raise NotImplementedError(_TODO)

    @property
    def memory(self) -> MemoryInterface:  # noqa: D102
        raise NotImplementedError(_TODO)

    def add_breakpoint(self, addr: int, *, hardware: bool = True) -> int:  # noqa: D102
        raise NotImplementedError(_TODO)

    def remove_breakpoint(self, breakpoint_id: int) -> None:  # noqa: D102
        raise NotImplementedError(_TODO)
