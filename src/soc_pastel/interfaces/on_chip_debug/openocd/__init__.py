"""On-chip debug through OpenOCD.

OpenOCD configuration is assembled from three fragments rather than one file
per chip, because each varies along a different axis:

* **transport** (per platform): how JTAG/SWD reaches the chip -- the
  simulator's ``remote_bitbang`` server, an FTDI cable, ...
* **cpu** (per CPU family): debug-module quirks. For VeeR: abstract memory
  access only (no program buffer), the custom ``dmst`` CSR exposed, and a
  fence through it before resume/step.
* **chain** (per device): TAP definitions -- IR length, expected IDCODE, and
  any non-standard DTM instruction codes (e.g. a BSCAN tunnel on an FPGA).

Fragments shipped with PASTEL live in ``transport/`` and ``cpu/``. Chain
fragments come from the device descriptor's binding config.
"""

from .config import OpenOcdConfig, fragment_path
from .driver import OpenOcd

__all__ = ["OpenOcd", "OpenOcdConfig", "fragment_path"]
