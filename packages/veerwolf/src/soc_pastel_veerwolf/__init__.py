"""PASTEL device pack for the VeeRwolf reference SoC."""

from pathlib import Path

from .device import VEERWOLF

#: FuseSoC cores of this pack (the PASTEL testbench for VeeRwolf).
HDL_ROOT: Path = Path(__file__).parent / "hw"

__all__ = ["HDL_ROOT", "VEERWOLF"]
