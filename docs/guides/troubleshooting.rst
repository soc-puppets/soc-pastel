Troubleshooting
===============

Where the logs are
------------------

.. list-table::
   :header-rows: 1

   * - Log
     - Location
   * - Simulator output
     - ``sim.log`` in the test's pytest temporary directory
   * - OpenOCD output and configuration
     - ``openocd.log``, ``openocd.cfg`` in the same directory
   * - Verilator build
     - ``.pastel/verilator/images/<top>-<hash>/build.log``
       (``<top>-<hash>-waves`` for the ``--waves`` model)
   * - Waveform (with ``--waves``)
     - ``sim.fst`` in the test's pytest temporary directory
   * - Firmware build
     - ``.pastel/firmware/<device>/<name>-<location>-<hash>/build.log``

pytest prints the temporary directory (``/tmp/pytest-of-$USER/...``) for failing
tests.

Building
--------

**Verilator build failed.**
The error shows the end of ``build.log``. Errors in your own files usually mean
a missing ``+incdir+`` or file in the ``sim_sources`` hook, or a top module that
isn't named in ``SimSources.top``.

**The ``--waves`` build fails with** ``'lz4.h' file not found``.
Recent Verilator needs LZ4 to write FST waveforms, and the compiler isn't
finding it. Install LZ4, and where it lives outside the default search path
(Homebrew on Apple silicon), point the compiler at it:
``export CPATH=/opt/homebrew/include LIBRARY_PATH=/opt/homebrew/lib``.

**The model rebuilds on every run.**
A listed file changes between runs in more than comments. Make the generating
step deterministic, or do it once and reuse the output (VeeRwolf's hook keeps
a marker file so FuseSoC set-up runs once).

**Firmware build failed.**
The compiler's errors are in the exception. ``riscv64-unknown-elf-gcc not found`` means the toolchain isn't on ``PATH`` or ``PASTEL_RISCV_PREFIX`` is wrong.

Connecting
----------

**``simulator exited before connecting``.**
The simulator died during elaboration or its first cycles; see ``sim.log``.

**``testbench and descriptor disagree``.**
The testbench's endpoints don't match the descriptor. Every disagreement is
listed: a region's ``backdoor`` name with no backdoor module, a backdoor whose
name isn't in the descriptor, or an interface VIP of the wrong type.

**``OpenOCD exited during start-up``.**
See ``openocd.log``. Typical causes: the ``chain`` Tcl has the wrong IR length,
or ``-expected-id`` doesn't match the TAP's IDCODE (the log prints the IDCODE
it found), or the testbench's JTAG wiring is wrong, in which case OpenOCD
reports all ones or all zeros.

**``target did not halt after reset``.**
Debug reached the TAP but the CPU didn't halt: check the reset wiring between
the debug module and the core (``dmi_hard_reset``, the core's reset input).

Running firmware
----------------

**Timeout waiting for the RTT control block.**
The firmware never reached ``test_main``. Check that ``reset_vector`` is right,
that the code location's regions are right, and that every peripheral region
has ``side_effects`` (wrong memory attributes can stall a VeeR core). If the
firmware faults before RTT is set up, the fault can't be reported; halt the
CPU and read ``pc``, ``mcause`` and ``mepc`` to see where it stopped.

**``failed to read memory`` while firmware runs.**
The memory can't be reached over debug while the CPU runs. For RTT, choose a
code location ``rtt`` region with a backdoor (simulation) or on the system bus
(hardware). In tests, wrap the access in ``harness.ocd.halted()``.

**Tests are slow.**
Loading over simulated JTAG takes seconds per kilobyte. Give the code
location's regions a backdoor so firmware loads through it. ``--durations=10``
shows where the time goes.
