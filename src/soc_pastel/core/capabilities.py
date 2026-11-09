"""Capability names.

A *capability* is something a test can require of the harness it runs on.
Tests declare requirements with ``@pytest.mark.requires(*names)`` and are
skipped, not failed, on a harness that lacks any of them.

There are two sources of capabilities:

* **Platform features** -- facilities some platforms have that do not
  correspond to anything on the chip's boundary. They are named by the
  constants below and reported by :meth:`Platform.capabilities`.
* **Interfaces** -- every interface the device descriptor binds for the
  current platform contributes its *instance name* (for example ``"spi0"``,
  ``"gpio"``, ``"on_chip_debug"``) as a capability.

Packs may define further platform-feature names; use a dotted prefix
(``"myvendor.thing"``) to avoid collisions.
"""

#: Direct access to target memory that bypasses the CPU and debug path.
BACKDOOR = "backdoor"

#: The platform reports its own notion of elapsed time (simulation time).
SIM_TIME = "sim_time"

#: The platform can dump waveforms on request.
WAVES = "waves"

#: All platform-feature names defined by the core.
CORE_PLATFORM_FEATURES = frozenset({BACKDOOR, SIM_TIME, WAVES})
