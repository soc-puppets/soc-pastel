"""Firmware runtime: C sources shipped as package data, and the host tools to use them.

* ``include/pastel/`` -- the firmware API (``pastel.h``), the mailbox protocol
  and the CPU port contract.
* ``runtime/`` -- RTT, mailbox, printf and start-up, shared by every port.
* ``ports/<cpu family>/`` -- CPU ports (``veer_el2``).
* :mod:`soc_pastel.fw.build` -- builds firmware for a device variant, generating
  ``memory_map.h`` and the linker script from the descriptor.
* :mod:`soc_pastel.fw.runner` -- loads, runs and collects results.

``pastel fw-path`` prints this directory for use in other build systems.
"""
