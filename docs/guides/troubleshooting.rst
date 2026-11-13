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
   * - Model build
     - ``build.log`` in the platform's build directory. For Verilator,
       ``.pastel/verilator/images/<top>-<hash>/build.log``
       (``<top>-<hash>-waves`` for the ``--waves`` model)
   * - Waveform (with ``--waves``)
     - In the test's pytest temporary directory, in the simulator's own
       format (``sim.fst`` with Verilator)
   * - Firmware build
     - ``.pastel/firmware/<device>/<name>-<location>-<hash>/build.log``

pytest prints the temporary directory (``/tmp/pytest-of-$USER/...``) for failing
tests.

Installing
----------

**``pip install -e`` fails with** ``editable mode currently requires a setuptools-based build``.
``pip`` belongs to a Python older than 3.10, or is itself older than 21.3.
Create the virtual environment with a suitable interpreter and an up-to-date
``pip`` (``python3.12 -m venv --upgrade-deps .venv``), activate it and install
again.

**A** ``fusesoc`` **command fails with** ``ValueError: Cannot use capturing groups in re.Scanner``.
The virtual environment was created with Python 3.15, which FuseSoC (through
its ``simplesat`` dependency) does not run on yet. Recreate the environment
with Python 3.10 to 3.13 and install again:
``rm -rf .venv && python3.12 -m venv --upgrade-deps .venv``.

**``pytest`` doesn't recognise** ``--device``, **or** ``pastel`` **is not found.**
The virtual environment PASTEL was installed into isn't active in this shell:
``source .venv/bin/activate``.

Building
--------

**The model build failed.**
The error shows the end of ``build.log``. Errors in your own files usually mean
a missing ``+incdir+`` or file in the ``sim_sources`` hook, or a top module that
isn't named in ``SimSources.top``.

**With Verilator, the ``--waves`` build fails with** ``'lz4.h' file not found``.
Recent Verilator needs LZ4 to write FST waveforms, and the compiler isn't
finding it. Install LZ4, and where it lives outside the default search path
(Homebrew on Apple silicon), point the compiler at it:
``export CPATH=/opt/homebrew/include LIBRARY_PATH=/opt/homebrew/lib``.

**``Verilator exited successfully but ... does not exist``.**
The build ran somewhere else (for example on a farm node, through a wrapper
given as ``--verilator``) and this machine's network file system hasn't seen
the model yet. PASTEL waits 30 seconds for it. Check the build machine wrote it
to the same path.

**The model doesn't rebuild after a toolchain change.**
The model's identity covers its sources, ``--verilator-args``, the
resolved ``verilator`` path and the compiler variables (``CXX``, ``CC``,
``CXXFLAGS``, ``CFLAGS``, ``CPPFLAGS``, ``LDFLAGS``, ``VERILATOR_ROOT``).
It can't see a new Verilator or compiler installed in place under the same
path. Use another ``--verilator-workdir``, or delete the image directory.

**``pastel: sidecar ... still running ... after finish was requested; killing it`` in ``sim.log``.**
OpenOCD didn't exit when the test ended, so each test waits about 5 seconds
before the simulator kills it. Check ``openocd.log`` for what it was doing.

**The model rebuilds on every run.**
A listed file changes between runs in more than comments. Make the generating
step deterministic, or do it once and reuse the output (VeeRwolf's hook keeps
a marker file so FuseSoC set-up runs once).

**Firmware build failed.**
The compiler's errors are in the exception. ``riscv64-unknown-elf-gcc not found`` means the toolchain isn't on ``PATH`` or ``PASTEL_RISCV_PREFIX`` is wrong.

Connecting
----------

**``simulator exited before connecting``.**
The simulator died during elaboration or its first cycles, or a launcher
failed (unknown host, job refused, missing run directory). The error shows the
end of ``sim.log``. A launcher that returns once the job is queued (``sbatch``,
plain ``qsub``) also gives this error; see :doc:`remote-simulation`.

**``simulator did not connect within ... s``.**
With a launcher, the job may still be queued (raise
``--sim-connect-timeout``), or the remote machine can't reach the test
machine. ``sim.log`` shows ``pastel: cannot connect to <host> (<ip>) ...`` for
each address it tried. Pass ``--sim-host-address`` with a name or IP the farm
can reach, and check that firewalls allow inbound TCP to the test machine.

**``ignored a connection from ...`` in the log.**
Something other than the simulator connected to the test process's port.
PASTEL ignores it and keeps waiting.

**``testbench and descriptor disagree``.**
The testbench's endpoints don't match the descriptor. Every disagreement is
listed: a region's ``backdoor`` name with no backdoor module, a backdoor whose
name isn't in the descriptor, or an interface VIP of the wrong type.

**``OpenOCD exited during start-up``.**
The error shows the end of ``openocd.log``. With ``(is it installed on the
simulation machine?)``, the simulator couldn't run OpenOCD: put it on the
simulation machine's ``PATH`` or set ``PASTEL_OPENOCD``. Otherwise, typical
causes are: the ``chain`` Tcl has the wrong IR length, or ``-expected-id``
doesn't match the TAP's IDCODE (the log prints the IDCODE it found), or the
testbench's JTAG wiring is wrong, in which case OpenOCD reports all ones or
all zeros.

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
