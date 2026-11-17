The firmware runtime
====================

Test firmware is built with a small runtime that PASTEL ships as package
data. It provides start-up code, RTT, the host mailbox, ``printf``, fault
reporting and the memory map.

The API
-------

.. code-block:: c

   #include "pastel/pastel.h"

.. list-table::
   :header-rows: 1

   * - Function
     - What it does
   * - ``int test_main(void)``
     - Defined by each test. Return 0 to pass.
   * - ``pastel_printf(fmt, ...)``
     - ``%d %i %u %x %X %p %s %c %%``, with ``0`` flag and width
   * - ``pastel_puts(s)``
     - Print a line
   * - ``PASTEL_INFO(fmt, ...)``
     - Print a line (``pastel_printf`` plus a newline)
   * - ``PASTEL_ERROR(fmt, ...)``
     - Report an error and carry on. The test fails.
   * - ``PASTEL_CHECK(expr)``, ``PASTEL_CHECK(expr, fmt, ...)``
     - If ``expr`` is false, report ``file.c:12: CHECK failed: expr: message``
       and carry on. The test fails.
   * - ``PASTEL_ASSERT(expr)``, ``PASTEL_ASSERT(expr, fmt, ...)``
     - Like ``PASTEL_CHECK``, then end the test at once
   * - ``pastel_snprintf(buf, size, fmt, ...)``
     - Format into a buffer
   * - ``pastel_checkpoint(id, wait)``
     - Report a checkpoint; if ``wait``, block until the host acknowledges
   * - ``pastel_waves(enable)``
     - Turn waveform dumping on or off and wait until it has happened; returns
       1 if waves are being dumped (see :ref:`waves`)
   * - ``pastel_send(data, len)``
     - Send up to 255 bytes to the host test
   * - ``pastel_finish(result)``
     - End the test now with ``result`` (what returning from ``test_main`` does)
   * - ``pastel_read32(addr)``, ``pastel_write32(addr, v)``
     - Volatile register access

Errors reach the host as they happen. They appear in the test's output at
once, and fail the test even if ``test_main`` returns 0. Use ``PASTEL_CHECK``
to check several things and see every failure, and ``PASTEL_ASSERT`` when going
on makes no sense. An error message longer than 255 bytes is cut short.

.. code-block:: c

   int test_main(void) {
     uint32_t id = pastel_read32(CHIP_ID);
     PASTEL_ASSERT(id != 0, "no chip ID");
     PASTEL_CHECK(id >> 16 == 0x1234, "vendor 0x%04x", id >> 16);
     PASTEL_CHECK((id & 0xffff) >= 2, "revision %u too old", id & 0xffff);
     return 0;
   }

There is no C library; ``memcpy``, ``memset``, ``memmove`` and ``memcmp`` are
provided. Output goes into a buffer the host drains while the firmware runs,
so ``pastel_printf`` blocks only if the host falls behind.

The memory map
--------------

Every build includes a generated ``memory_map.h``, so firmware never hard-codes
addresses:

.. code-block:: c

   PASTEL_DEVICE           // "veerwolf"
   PASTEL_VARIANT          // "sim"
   PASTEL_<REGION>_BASE    // e.g. PASTEL_UART_BASE
   PASTEL_<REGION>_SIZE

The linker script is generated too, from the code location:

.. list-table::
   :header-rows: 1

   * - Section
     - Placed in
   * - ``.text``
     - The code location's ``text`` region
   * - ``.rodata``, ``.data``, ``.bss``, stack
     - Its ``data`` region (text regions may be execute-only)
   * - ``.rtt`` (RTT control block and buffers)
     - Its ``rtt`` region

Firmware is loaded directly into place: ``.data`` needs no copying, and the
start-up code clears ``.bss``.

The chip's own headers
----------------------

A device pack can add include directories and defines to every firmware
build of a variant, for example so test firmware takes register addresses
from the header a SystemRDL flow generates rather than from hand-written
``#define``\ s. Name a hook in the variant's params:

.. code-block:: python

   from soc_pastel.fw.build import FirmwareOptions

   def fw_build(variant):
       return FirmwareOptions(
           include_dirs=(REPO / "software" / "firmware" / "my_chip",),
           defines=("MY_CHIP_SIM=1",),
       )

   SIM = Variant(name="sim", ..., params={"fw_build": "my_pack.fw:fw_build"})

Test firmware can then ``#include "my_chip_addrmap.h"``. The directories are
searched after PASTEL's own headers, so they can't hide them, and before a
test's ``include_dirs``.

.. _how-firmware-is-built:

How firmware is built
---------------------

**When:** each time a test calls ``firmware.build()`` or ``firmware.run()``.
``firmware.run()`` builds first and only then touches the target, so the
firmware is compiled before the simulator starts. A compile error fails the
test in about a second, without starting a simulator or, with a farm
launcher, submitting a job.

**Where:** on the machine running pytest, into the test's run directory:

.. code-block:: text

   <run directory>/fw/<first source>-<code location>/
       <first source>-<code location>.elf
       build.log          the compiler command line, then its output
       gen/memory_map.h   generated from the descriptor
       gen/link.ld        generated from the code location

**What:** one ``gcc`` call compiles and links PASTEL's runtime (start-up code,
RTT, the mailbox, ``printf``) and the test's sources together. It uses:

- ``-march``/``-mabi`` from the device's CPU, with ``-Os -g -Wall``
- the generated ``memory_map.h``, included in every file
- the include directories, searched in this order: the generated header, PASTEL's own,
  the variant's ``fw_build`` hook, then the test's ``include_dirs``
- the defines from the hook, then the test's

**Nothing is cached.** Every build starts from scratch, so a test always runs
firmware built from the sources and headers as they are now. A build takes
about a second.

**Errors and warnings:** a compile error fails the test with the compiler's
messages and the path of ``build.log``. Warnings don't fail the test, but they
are printed as soon as the build finishes, and ``build.log`` is listed if the
test fails.

Results and faults
------------------

The value ``test_main`` returns is the test result. An unexpected exception goes
to the port's trap handler, which reports the cause, PC and trap value to the
host; the test fails with a
:py:class:`~soc_pastel.fw.runner.FirmwareFault` instead of hanging. A fault
before RTT is initialised cannot be reported and shows up as a timeout.

CPU ports
---------

A port provides the start-up code and trap entry for a CPU family
(``fw/ports/<family>/``). The ``veer_el2`` port:

-  programs MRAC first thing, from the descriptor: every 256 MiB region that
   contains a region with ``side_effects`` is marked side-effect. PASTEL loads
   code and sets the PC itself, bypassing any boot ROM that would otherwise do
   this;
-  sets up the stack, global pointer and trap vector, clears ``.bss``, and calls
   the runtime.

Adding a port for another CPU family means a new ``fw/ports/<family>/``
(start-up code and ``port.c``, see ``pastel/port.h``) and, where the port needs
values derived from the descriptor, an entry in
``soc_pastel.fw.build.PORT_DEFINES``. Ports are part of PASTEL itself.

Toolchain
---------

Firmware is built with a GNU RISC-V toolchain: ``riscv64-unknown-elf-`` by
default, or the prefix in ``PASTEL_RISCV_PREFIX``. Flags come from the
descriptor's ``CpuSpec`` (``-march``, ``-mabi``). Use the same toolchain in other
build systems with:

.. code-block:: shell

   pastel fw-path      # the runtime's directory
