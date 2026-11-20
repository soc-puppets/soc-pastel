"""PASTEL: Platform Agnostic Silicon Test Environment & Library.

Write chip-level tests once and run them on simulation, FPGA and silicon.
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("soc-pastel")  # set from the git tag at build time
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
    __version__ = "0+unknown"

#: Root directory searched for this package's FuseSoC cores (HDL, VIPs).
HDL_ROOT: Path = Path(__file__).parent

#: Root directory of the firmware runtime (C sources, headers, makefile fragments).
FW_ROOT: Path = Path(__file__).parent / "fw"
