Command line and configuration
==============================

pytest options
--------------

.. list-table::
   :header-rows: 1

   * - Option
     - Meaning
   * - ``--device=<name>``
     - Device to test. Optional when exactly one device pack is installed.
   * - ``--platform=<name>``
     - Platform to run on.
   * - ``--variant=<name>``
     - Device variant; normally chosen from the platform.
   * - ``--code-location=<name>``
     - Where firmware runs; defaults to the variant's first code location.
   * - ``--waves``
     - Dump waveforms from the start of each test, on platforms that can
       (see :ref:`waves`).
   * - ``--verilator-workdir=<dir>``
     - Where Verilator models are built (default ``.pastel/verilator``).
   * - ``--verilator=<exe>``
     - The Verilator executable.
   * - ``--sim-launcher=<template>``
     - Run the simulator through a command: a prefix (``srun -p sim``) or a
       template with ``{cmd}`` (``ssh simbox {cmd}``). See
       :doc:`../guides/remote-simulation`.
   * - ``--sim-launcher-class=<name>``
     - Run the simulator with a :class:`~soc_pastel.sim.launch.SimLauncher`
       plugin: a ``soc_pastel.launchers`` entry-point name or ``module:Class``.
   * - ``--sim-host-address=<addr>``
     - Address a remote simulator dials to reach the test machine (default:
       its fully qualified host name). Only used with a launcher.
   * - ``--sim-connect-timeout=<seconds>``
     - How long to wait for the simulator to connect (default 120, or 3600
       with a launcher).

Markers
-------

``@pytest.mark.requires(*capabilities)``
   Skip the test unless the harness offers every capability: interface
   instance names, or ``backdoor``, ``sim_time``, ``waves``.

Fixtures
--------

``harness``
   An open :py:class:`~soc_pastel.core.harness.PlatformHarness`, per test.
``firmware``
   Builds and runs firmware on that harness; sources are relative to the test
   file.
``pastel_device``
   The :py:class:`~soc_pastel.core.device.Device` under test (session scope).

The ``pastel`` command
----------------------

.. list-table::
   :header-rows: 1

   * - Command
     - Does
   * - ``pastel list``
     - List installed devices and platforms
   * - ``pastel describe <device> [--variant V]``
     - Print a device's memory map, code locations and bindings
   * - ``pastel cores-path``
     - FuseSoC core roots of installed packs, for ``--cores-root`` or ``FUSESOC_CORES``
   * - ``pastel fw-path``
     - The firmware runtime directory

Environment variables
---------------------

.. list-table::
   :header-rows: 1

   * - Variable
     - Meaning
   * - ``PASTEL_RISCV_PREFIX``
     - Toolchain prefix (default ``riscv64-unknown-elf-``)
   * - ``PASTEL_OPENOCD``
     - OpenOCD executable (default ``openocd``)
   * - ``PASTEL_VEERWOLF_WORKSPACE``
     - Existing FuseSoC workspace for the VeeRwolf pack

Simulator plusargs
------------------

Set by PASTEL; listed for running a model by hand.

.. list-table::
   :header-rows: 1

   * - Plusarg
     - Meaning
   * - ``+pastel_host=<host>:<port>:<token>``
     - Where to connect: a host name, IPv4 address or bracketed IPv6 address
       (``[fd00::1]``). Without it the model runs standalone.
   * - ``+pastel_jtag_port=<n>``
     - JTAG server port (default: any free port, reported in HELLO)
   * - ``+pastel_jtag_bind=<addr>``
     - Address the JTAG server listens on (default ``127.0.0.1``;
       ``0.0.0.0`` for every interface, IPv4 and IPv6). Set with a launcher.
   * - ``+pastel_clk_period_ps=<n>``
     - Clock period of ``pastel_clock`` (default 20000, i.e. 50 MHz)
   * - ``+pastel_waves=<file>``
     - Dump an FST waveform to ``<file>``. Needs a model built with waves
       (an image whose name ends in ``-waves``).

Entry-point groups
------------------

.. list-table::
   :header-rows: 1

   * - Group
     - Name
     - Value
   * - ``soc_pastel.devices``
     - device name
     - a ``Device`` instance
   * - ``soc_pastel.platforms``
     - platform name
     - a ``Platform`` subclass
   * - ``soc_pastel.interfaces``
     - ``<type>/<impl>``
     - an ``Interface`` subclass
   * - ``soc_pastel.hdl``
     - pack name
     - a ``Path`` containing FuseSoC cores
   * - ``soc_pastel.launchers``
     - launcher name
     - a :class:`~soc_pastel.sim.launch.SimLauncher` subclass

Variant parameters
------------------

The core never interprets ``Variant.params``; platforms document the keys they
read.

``sim_sources``
   ``"module:function"`` returning
   :py:class:`~soc_pastel.sim.sources.SimSources`. Read by simulation platforms.
