Concepts
========

Three things vary independently
-------------------------------

Every test run combines three independent choices:

**Device**: what the chip is.
   Its CPU, memory map, interfaces and register model. Described by a
   :py:class:`~soc_pastel.core.device.Device` in a device pack.
**Platform**: where it runs.
   Verilator, an FPGA board, a silicon bench. A
   :py:class:`~soc_pastel.core.platform.Platform` plugin.
**Interfaces**: how the test reaches the chip's boundary.
   On-chip debug, GPIO, SPI, UART. Each has a platform-neutral contract and an
   implementation per platform: a simulation VIP, a probe, a bench adapter.

PASTEL composes them at run time from the command line:

.. code-block:: shell

   pytest --device=veerwolf --platform=verilator

The harness
-----------

A test receives a :py:class:`~soc_pastel.core.harness.PlatformHarness`, which
bundles the device, the variant in use, the platform and the interfaces bound
for that platform:

.. list-table::
   :header-rows: 1

   * - Attribute
     - What it is
   * - ``harness.ocd``
     - The on-chip debug interface (reset, halt, registers, memory)
   * - ``harness.memory()``
     - The fastest memory path: the backdoor in simulation, debug otherwise
   * - ``harness.iface("spi0")``
     - Any other interface, by instance name
   * - ``harness.regs``
     - The register model, if the device has one
   * - ``harness.device``, ``.variant``, ``.platform``
     - The descriptor and the running platform

Devices, variants and code locations
------------------------------------

One chip is often built differently for different platforms. VeeRwolf's
simulation build has 64 KiB ICCM and DCCM; its FPGA builds have neither. A
:py:class:`~soc_pastel.core.device.Variant` describes one build: its memory
regions, where firmware may be linked
(:py:class:`~soc_pastel.core.device.CodeLocation`), and build parameters.
The variant is chosen from the platform unless ``--variant`` says otherwise.

The memory map lives in the descriptor and nowhere else. Firmware headers and
linker scripts are generated from it.

Interfaces and platform features
--------------------------------

An **interface** is something on the chip's boundary, which therefore exists
on every platform in some form: on-chip debug, GPIO, SPI, UART.

A **platform feature** is something only some platforms have and which is not
on the chip's pins: backdoor memory access, simulation time, waveforms.

Both are *capabilities*. A test declares what it needs and is skipped, not
failed, where that is unavailable:

.. code-block:: python

   @pytest.mark.requires("backdoor", "spi0")
   def test_flash_id(harness): ...

On-chip debug is special
------------------------

It is the one interface the framework itself depends on: loading firmware,
reading RTT and frontdoor memory access all go through it. Every device must
bind an ``on_chip_debug`` interface, and the harness opens it before any other.

Simulation
----------

A simulation runs as its own process. On the first clock edge the testbench's
``pastel_server`` dials back to the test process and announces its *endpoints*:
one per VIP, plus the platform's own (backdoor memories and platform
control). Endpoints exchange opaque byte messages; a small C++ pump inside the
simulator moves them, and all interpretation happens in the test process. No
Python runs inside the simulator.

OpenOCD runs beside the test process and talks JTAG to the testbench's
``pastel_jtag`` over OpenOCD's ``remote_bitbang`` protocol.

The simulator need not run on the test machine. A launcher (``srun``,
``ssh``, or a plugin) can start it anywhere that can phone home over TCP. See
:doc:`guides/remote-simulation`.

Loading firmware over simulated JTAG is slow (seconds per kilobyte), so
regions with a backdoor are loaded through it instead (milliseconds). Debug
access still works everywhere, as it must on hardware.

Firmware tests
--------------

Firmware is built with the PASTEL runtime for the device, variant and code
location in use, loaded, and run. It reports through RTT: text on channel 0,
and a mailbox on channel 1 carrying checkpoints, data, the result of
``test_main()`` and any fault. See :doc:`guides/firmware`.
