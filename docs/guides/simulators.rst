Simulators
==========

PASTEL's testbench HDL and DPI C++ work with any SystemVerilog simulator that
supports DPI. Each simulator is a platform, chosen with ``--platform``:

.. list-table::
   :header-rows: 1

   * - Platform
     - Status
     - Model
     - Waves (``--waves``)
   * - ``verilator``
     - Supported; used by CI
     - ``Vsim``, from ``verilator --binary``
     - ``sim.fst`` (whole run: Verilator can't pause dumping)
   * - ``xcelium``
     - Supported
     - An elaborated snapshot, run with ``xrun -R``
     - ``sim.trn`` and ``sim.dsn`` (SimVision; open ``sim.trn``)
   * - ``vcs``
     - **Experimental: untested**
     - ``simv``, from ``vcs -full64 -sverilog``
     - ``sim.vcd``
   * - ``questa``
     - **Experimental: untested**
     - A ``vlog``/``vopt`` design, run with ``vsim -c``
     - ``sim.vcd``

The VCS and Questa platforms follow each tool's documented command-line flow,
but nobody has run them yet. If you have either, please try them and report
what needed changing.

What every simulator platform shares
------------------------------------

- **Models are built once and reused.** A model lives in
  ``.pastel/<platform>/images/<top>-<hash>/``, with its ``build.log``. The hash
  covers everything that can change the model: the sources and the headers in
  their include directories (by content), PASTEL's own HDL and C++, the extra
  arguments, which tool runs, and the compiler environment variables.
  ``--<platform>-workdir`` moves it, e.g. to a shared file system.
- **Extra arguments** come from ``--<platform>-args`` on the command line, and
  from ``SimSources.tool_args["<platform>"]`` in the device pack for arguments
  one simulator always needs. Changing either builds a new model.
- **Source arguments are translated.** ``-D<name>`` becomes ``+define+<name>``
  for simulators other than Verilator, and Verilator's ``.vlt`` files are
  dropped.
- **Launching** works the same everywhere, locally or through a launcher (see
  :doc:`remote-simulation`). The simulator's output goes to ``sim.log`` in the
  test's run directory.

Logs are checked
----------------

Every model's build log, and every test's ``sim.log``, are checked for the
simulator's error and warning messages:

- **Build:** any error fails the build, even if the tool exited successfully.
  Warnings are reported when the model is built, and once per run of pytest
  after that, with the path of ``build.log``.
- **Run:** once the simulator has exited, any error in ``sim.log`` fails the
  test. That includes ``$error``/``$fatal`` messages, assertion failures,
  ``UVM_ERROR``/``UVM_FATAL`` and ``OVL_ERROR``, and a failing exit status.
  The test fails itself, not as an error in teardown. If the test had already
  failed, the log errors are reported but don't replace its own failure.
  Warnings are reported.

.. list-table::
   :header-rows: 1

   * - Platform
     - Errors
     - Warnings
   * - ``verilator``
     - ``%Error``, ``%Fatal``
     - ``%Warning``
   * - ``xcelium``
     - ``*E,``, ``*F,``; the build must print "Writing initial simulation
       snapshot"
     - ``*W,``
   * - ``vcs``
     - ``Error-[...]``, ``Error: "``, ``Fatal: "``
     - ``Warning-[...]``, ``Lint-[...]``, ``Warning: "``
   * - ``questa``
     - ``** Error``, ``** Fatal``
     - ``** Warning``

Builds also check C and C++ compiler diagnostics (``error:``, ``warning:``).
Every run also checks UVM and OVL messages and PASTEL's own (``pastel: cannot
...``). UVM's end-of-test summary lines with a count of 0 aren't errors.

A device pack adds its own rules, or waives harmless messages, in its
``sim_sources`` hook; see :ref:`adding log rules <log-rules>`.

Verilator
---------

``--verilator=<exe>``, ``--verilator-args=<args>``. The model is a native
executable built by Verilator and the C++ compiler, so it runs wherever its
libraries are available.

Xcelium
-------

``--xrun=<exe>``, ``--xcelium-args=<args>`` (extra elaboration arguments, e.g.
``-uvm``), ``--xcelium-build-prefix=<cmd>``.

The model is built in two steps:

1. PASTEL's DPI C++ is compiled into ``libpastel_dpi.so`` with ``$CXX``
   (default ``g++``), against Xcelium's ``svdpi.h``. PASTEL finds that from
   ``$XCELIUM_HOME`` or ``xmroot``.
2. ``xrun -elaborate`` writes a snapshot. It runs under
   ``--xcelium-build-prefix`` if given, e.g. ``--xcelium-build-prefix="srun -c 8"``
   to elaborate on a compute farm. The elaboration fails if ``xrun`` reports
   any error or never writes the snapshot, whatever its exit status, because
   batch wrappers don't always pass that on.

A ``run`` script then calls ``xrun -R`` on the snapshot. The simulation writes
its own log, ``xrun.log``, in the run directory, as well as ``sim.log``.

VCS (experimental)
------------------

``--vcs=<exe>``, ``--vcs-args=<args>``. One ``vcs`` call compiles the HDL and
PASTEL's DPI C++ into ``simv``. Any ``Error-[...]`` message fails the build.

Questa (experimental)
---------------------

``--vsim=<exe>``, ``--questa-args=<args>`` (extra ``vlog`` arguments).
``vlib``, ``vlog`` and ``vopt`` are taken from beside ``vsim``. The DPI
library is compiled with ``$CXX`` against Questa's ``svdpi.h``, found from
``$QUESTA_HOME``, ``$MTI_HOME`` or the installation ``vsim`` belongs to. A
``run`` script calls ``vsim -c -onfinish exit``. Any ``** Error`` or
``** Fatal`` message fails the build.

Adding a simulator
------------------

Subclass :py:class:`~soc_pastel.sim.simulator.SimulatorImage`: set ``tool``,
and provide ``exe`` and ``steps()``, the build commands. Use ``check_step()``
and ``finish()`` if it needs them. Then subclass
:py:class:`~soc_pastel.sim.simulator.SimulatorPlatform`, providing ``name``,
``WAVES_FILE``, ``add_options()`` and ``make_image()``, and register it under the
``soc_pastel.platforms`` entry-point group. :mod:`soc_pastel.sim.verilator` is the
smallest example.
