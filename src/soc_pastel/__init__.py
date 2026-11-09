"""PASTEL: Platform Agnostic Silicon Test Environment & Library.

Write chip-level tests once and run them on simulation, FPGA and silicon.
"""

from pathlib import Path

__version__ = "0.1.0.dev0"

#: Root directory searched for this package's FuseSoC cores (HDL, VIPs).
HDL_ROOT: Path = Path(__file__).parent

#: Root directory of the firmware runtime (C sources, headers, makefile fragments).
FW_ROOT: Path = Path(__file__).parent / "fw"
