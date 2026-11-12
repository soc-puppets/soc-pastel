Writing tests
=============

PASTEL tests are ordinary pytest tests. The same test runs on every platform
the device supports; PASTEL supplies the platform-specific parts.

Host tests
----------

A test that drives the chip from the host takes the ``harness`` fixture:

.. code-block:: python

   def test_scratch_memory(harness):
       harness.ocd.reset(halt=True)
       harness.ocd.memory.write32(0x1000, 0x1234_5678)
       assert harness.memory().read32(0x1000) == 0x1234_5678

Each test gets its own harness: a fresh simulation, or a freshly reset target
on hardware. On-chip debug is open before the test starts.

``harness.ocd`` is the :py:class:`~soc_pastel.core.on_chip_debug.OnChipDebug`
interface: ``reset``, ``halt``, ``resume``, ``step``, ``state``, ``read_register``,
``write_register``, breakpoints, and ``memory`` for access through the debug port.

``harness.memory()`` returns the fastest
:py:class:`~soc_pastel.core.memory.MemoryInterface`: the backdoor in
simulation, on-chip debug otherwise. Addresses and lengths are always bytes;
``read32``, ``write32``, ``read_words`` and ``write_words`` are conveniences on top.

Memory reachable only while halted
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some memories can only be accessed over debug while the CPU is halted (VeeR's
DCCM, for one). Wrap such accesses in ``halted()``, which halts the CPU if it is
running and resumes it afterwards:

.. code-block:: python

   with harness.ocd.halted():
       value = harness.ocd.memory.read32(dccm_address)

Firmware tests
--------------

Most chip-level tests run code on the chip. Write a C file defining
``test_main`` next to the test, and use the ``firmware`` fixture:

.. code-block:: c

   // fw/uart_loopback.c
   #include "pastel/pastel.h"

   int test_main(void) {
     pastel_printf("UART at 0x%08x\n", PASTEL_UART_BASE);
     // ... exercise the hardware ...
     return 0;   // 0 passes; anything else fails
   }

.. code-block:: python

   def test_uart_loopback(firmware):
       result = firmware.run("fw/uart_loopback.c")
       assert result.passed, result.output

``firmware.run()`` builds the firmware for the device, variant and code location
in use, resets the target, loads it, runs it and waits for it to finish. It
returns a :py:class:`~soc_pastel.fw.runner.FirmwareResult`:

.. list-table::
   :header-rows: 1

   * - Field
     - Contents
   * - ``result``
     - ``test_main``'s return value
   * - ``passed``
     - ``result == 0``
   * - ``output``
     - Everything printed with ``pastel_printf``
   * - ``checkpoints``
     - Checkpoint ids, in order
   * - ``data``
     - Payloads sent with ``pastel_send``, in order

An unexpected exception in the firmware raises
:py:class:`~soc_pastel.fw.runner.FirmwareFault`, with ``cause``, ``pc`` and
``tval``. Not finishing within ``timeout_s`` (default 300 s, wall clock) raises
:py:class:`~soc_pastel.core.errors.PastelTimeout`.

Doing things at a known point
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A checkpoint pauses the firmware until the host has acted:

.. code-block:: c

   pastel_checkpoint(1, 1);   // id 1; wait for the host

.. code-block:: python

   def test_interrupt(firmware):
       def on_checkpoint(cp):
           if cp == 1:
               firmware.harness.memory().write32(TRIGGER, 1)

       assert firmware.run("fw/interrupt.c", on_checkpoint=on_checkpoint).passed

To share variables between firmware and host, build first and look up the
symbol:

.. code-block:: python

   from soc_pastel.core.loader import elf_symbol

   elf = firmware.build("fw/shared.c")
   addr = elf_symbol(elf, "shared_buffer")

then run it with :py:func:`~soc_pastel.fw.runner.run_firmware`.

Build options
~~~~~~~~~~~~~

``firmware.build()`` and ``firmware.run()`` accept several sources, plus
``include_dirs``, ``defines`` (strings such as ``"N_SAMPLES=16"``) and
``stack_size``. Builds are cached by content under ``.pastel/firmware/``.

Code locations
~~~~~~~~~~~~~~

Firmware is linked for the code location given by ``--code-location``, or the
variant's first. Run a suite at each location the device declares:

.. code-block:: shell

   pytest tests/ --device=veerwolf --platform=verilator --code-location=ccm

Declaring requirements
----------------------

A test that needs something only some platforms have declares it, and is
skipped elsewhere:

.. code-block:: python

   import pytest

   @pytest.mark.requires("backdoor")
   def test_fill_fast(harness): ...

   @pytest.mark.requires("spi0")
   def test_flash_id(harness, firmware): ...

Capabilities are interface instance names from the device descriptor
(``"spi0"``, ``"gpio"``) and platform features: ``"backdoor"``, ``"sim_time"``,
``"waves"``.

.. _waves:

Driving the chip's pins
-----------------------

Interfaces are reached by their instance name in the descriptor. GPIO
samples, drives and releases pins, with bit ``n`` for pin ``n``:

.. code-block:: python

   from soc_pastel.interfaces.gpio import Gpio

   @pytest.mark.requires("gpio")
   def test_button_wakes_the_chip(harness, firmware):
       gpio = harness.iface("gpio", Gpio)

       def on_checkpoint(cp):
           if cp == 1:                      # firmware is waiting for the button
               gpio.drive(1 << 4, 1 << 4)   # press it: pin 4 high
           elif cp == 2:
               gpio.release(1 << 4)

       assert firmware.run("fw/wakeup.c", on_checkpoint=on_checkpoint).passed

``drive`` and ``release`` return once the chip can see the change, so firmware
released from a checkpoint, or a register read over debug, sees the new
level. In simulation an X or Z pin samples as 0, and the simulation
implementation's ``sample_unknown()`` says which pins those are. A 2-state
simulator such as Verilator has no X or Z, so an undriven pin is just 0 there.

Waveforms
---------

Run with ``--waves`` to dump waveforms on platforms that can:

.. code-block:: shell

   pytest tests/ --device=<device> --platform=<platform> --waves

The simulator writes a waveform covering the whole testbench, in its own
format, next to ``sim.log`` in each test's temporary directory (``sim.fst``
with Verilator). Where tracing has to be compiled in, as with Verilator, the
first run with ``--waves`` builds a second, slower model; runs without it keep
using the fast one. The end of the run lists each test's waveform.

Dumping starts at time 0. To keep a waveform small, turn dumping off and on
around the part you care about, from the test or from firmware:

.. code-block:: python

   @pytest.mark.requires("waves")
   def test_wakeup(harness, firmware):
       harness.platform.set_waves(False)
       ...
       harness.platform.set_waves(True)

.. code-block:: c

   pastel_waves(0);
   boot_slowly();
   pastel_waves(1);

Both return once the platform has made the change, and say whether waves are
now being dumped. Without ``--waves``, or on a platform that cannot dump waves,
they do nothing, so a test only needs ``requires("waves")`` if it is pointless
without a waveform.

.. note::

   Not every simulator can pause a waveform. Verilator ignores ``$dumpoff``
   and ``$dumpon``, so on Verilator the calls above are accepted and return
   true, and the waveform covers the whole run.

Running tests
-------------

.. code-block:: shell

   pytest tests/ --device=<device> --platform=<platform>
   pytest tests/ --device=<device> --platform=<platform> -n 8   # in parallel

Each test's logs (``sim.log``, ``openocd.log``, ``openocd.cfg``) are in its pytest
temporary directory; pytest prints its path when a test fails.
