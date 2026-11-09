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
   * - ``pastel_checkpoint(id, wait)``
     - Report a checkpoint; if ``wait``, block until the host acknowledges
   * - ``pastel_send(data, len)``
     - Send up to 255 bytes to the host test
   * - ``pastel_finish(result)``
     - End the test now with ``result`` (what returning from ``test_main`` does)
   * - ``pastel_read32(addr)``, ``pastel_write32(addr, v)``
     - Volatile register access

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
