Getting started
===============

Prerequisites
-------------

PASTEL itself is pure Python. Running tests in simulation needs:

.. list-table::
   :header-rows: 1

   * - Tool
     - Notes
   * - Python 3.10+
     - 
   * - Verilator 5
     - The simulator PASTEL supports so far. Tested with 5.020 (Ubuntu 24.04
       package)
   * - RISC-V GCC
     - ``riscv64-unknown-elf-`` prefix by default; set ``PASTEL_RISCV_PREFIX`` otherwise
   * - OpenOCD 0.12+
     - With the ``remote_bitbang`` driver (stock builds have it); set ``PASTEL_OPENOCD`` to choose a binary
   * - Git, FuseSoC
     - FuseSoC is installed with PASTEL

The VeeRwolf device pack also needs Perl's ``Bit::Vector`` module, which the
VeeR EL2 configuration generator uses (``libbit-vector-perl`` on Debian/Ubuntu).

On Ubuntu 24.04:

.. code-block:: shell

   sudo apt-get install verilator gcc-riscv64-unknown-elf openocd libbit-vector-perl

Install and run the conformance suite
-------------------------------------

Until the first release on PyPI, install from the repository.

**Step 1: clone the repository.**

.. code-block:: shell

   git clone https://github.com/soc-puppets/soc-pastel.git
   cd soc-pastel

**Step 2: create and activate a virtual environment.**

.. code-block:: shell

   python3 -m venv .venv
   source .venv/bin/activate

The virtual environment keeps PASTEL and its dependencies out of your system
Python. The first command creates it once, in ``.venv``. The second selects it
for the current shell, so run it again in every new terminal before using
``pytest`` or ``pastel``. (In csh or tcsh, source ``.venv/bin/activate.csh``
instead.)

.. note::

   The environment uses whichever Python created it, and PASTEL needs 3.10 or
   newer. If ``python3 --version`` reports something older, name a newer
   interpreter explicitly, e.g. ``python3.12 -m venv .venv``. An old Python
   makes step 3 fail with a message about editable mode needing a
   setuptools-based build.

**Step 3: install the PASTEL core.**

.. code-block:: shell

   pip install -e .

``-e`` installs in *editable* mode: Python uses the sources in your checkout
directly, so changes take effect without reinstalling. Reinstall only when a
``pyproject.toml`` changes.

**Step 4: install a device pack.**

.. code-block:: shell

   pip install -e packages/veerwolf

This is the pack for the VeeRwolf reference SoC, which lives in this
repository. ``pastel list`` should now show the ``veerwolf`` device and the
platforms it can run on.

**Step 5: run the conformance suite.**

.. code-block:: shell

   pytest --pyargs soc_pastel.conformance --device=veerwolf --platform=verilator

The first run fetches VeeRwolf (pinned to a tested commit) and its
dependencies, and builds the simulation model: allow a few minutes. Later runs
reuse both. Everything is cached under ``.pastel/`` in the current directory;
add it to ``.gitignore``.

Useful options:

.. list-table::
   :header-rows: 1

   * - Option
     - Meaning
   * - ``--device``
     - Device pack to use (``pastel list`` shows installed ones)
   * - ``--platform``
     - Platform to run on (``pastel list`` shows installed ones)
   * - ``--variant``
     - Device build; normally chosen from the platform
   * - ``--code-location``
     - Where firmware runs, e.g. ``ram`` or ``ccm`` for VeeRwolf
   * - ``-n N``
     - Run tests in parallel (with ``pytest-xdist``)

Each test's simulator and OpenOCD logs are in its pytest ``tmp_path``
(``/tmp/pytest-of-$USER/...``); see :doc:`guides/troubleshooting`.

Writing a test
--------------

A host-side test receives a harness:

.. code-block:: python

   def test_scratch_register(harness):
       harness.ocd.reset(halt=True)
       harness.ocd.memory.write32(0x1000, 0x1234)
       assert harness.memory().read32(0x1000) == 0x1234  # backdoor in simulation

A firmware test pairs a C file with a few lines of Python:

.. code-block:: c

   // fw/blink.c
   #include "pastel/pastel.h"

   int test_main(void) {
     pastel_printf("running from 0x%08x\n", (unsigned)(uintptr_t)&test_main);
     return 0;   // non-zero fails the test
   }

.. code-block:: python

   def test_blink(firmware):
       result = firmware.run("fw/blink.c")
       assert result.passed, result.output

Declare what a test needs and it is skipped where that isn't available:

.. code-block:: python

   @pytest.mark.requires("backdoor")
   def test_fast_fill(harness): ...

Inspecting a device
-------------------

.. code-block:: shell

   pastel list
   pastel describe veerwolf

Next steps
----------

-  :doc:`concepts` explains how the pieces fit together.
-  :doc:`guides/writing-tests` covers host and firmware tests in depth.
-  :doc:`guides/device-pack` brings up a new chip.
