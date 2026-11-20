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
:py:class:`~soc_pastel.core.memory.MemoryInterface` for each address: the
backdoor in simulation where one reaches that memory, on-chip debug otherwise. Addresses and lengths are always bytes;
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
     - ``result == 0`` and no ``errors``
   * - ``errors``
     - Errors the firmware reported (``PASTEL_ERROR``, a failed
       ``PASTEL_CHECK`` or ``PASTEL_ASSERT``), in order
   * - ``output``
     - Everything printed with ``pastel_printf``
   * - ``checkpoints``
     - Checkpoint ids, in order
   * - ``data``
     - Payloads sent with ``pastel_send``, in order

If the firmware fails, ``firmware.run()`` fails the test with
:py:class:`~soc_pastel.fw.runner.FirmwareFailed`. The message gives the
result, every error the firmware reported, and the end of its output. A test
that checks a failure passes ``expect_pass=False`` and gets the result back
instead:

.. code-block:: python

   def test_bad_config_is_rejected(firmware):
       result = firmware.run("fw/bad_config.c", expect_pass=False)
       assert result.result == 3

A firmware's errors are then logged as ``XERROR``, an *expected* error, rather
than ``ERROR``, like pytest's ``xfail``.

An exception in the firmware raises
:py:class:`~soc_pastel.fw.runner.FirmwareFault`, with ``cause``, ``pc`` and
``tval``. A test that expects one passes ``expect_fault=True``. The fault is
then logged as ``XERROR`` and returned in ``result.fault``, and the test fails
if the firmware finishes without one:

.. code-block:: python

   def test_illegal_instruction_traps(firmware):
       result = firmware.run("fw/illegal.c", expect_fault=True)
       assert result.fault.cause == 2

PASTEL only knows an error is expected if the test says so in
``firmware.run()``. Errors caught with ``pytest.raises`` are still logged as
``ERROR``. Not finishing within ``timeout_s`` (default 300 s, wall clock) raises
:py:class:`~soc_pastel.core.errors.PastelTimeout`.

How a test passes or fails
~~~~~~~~~~~~~~~~~~~~~~~~~~

A test passes only if all of these hold:

- **Its own assertions pass.** It's a pytest test.
- **The firmware builds.** Errors fail the test with the compiler's messages.
  Warnings are printed but don't fail it.
- **The firmware passes:** ``test_main`` returns 0 and nothing reported an
  error. ``firmware.run()`` checks this unless given ``expect_pass=False``.
- **The firmware finishes in time and doesn't fault.**
- **The model builds cleanly:** no errors in its build log.
- **The simulation is clean:** once the test has finished, ``sim.log`` has no
  error messages (``$error``, ``$fatal``, assertion failures, ``UVM_ERROR``,
  ...) and the simulator exited successfully, apart from errors the test
  expected with ``harness.expect_sim_error(pattern)``. See :doc:`simulators`.

Doing things at a known point: checkpoints
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A *checkpoint* is the firmware telling the test "I've got this far". It
carries a number you choose, the id. With ``wait`` set, the firmware also stops
until the test says carry on, so the test can act at exactly that point:
drive a pin, write a register, or look at memory, knowing the firmware isn't
running past it.

.. code-block:: c

   pastel_checkpoint(1, 1);   // id 1; wait for the test
   pastel_checkpoint(2, 0);   // id 2; don't wait: just a marker

On the test side, ``firmware.run(..., on_checkpoint=f)`` calls ``f(id)`` for
each checkpoint, in order, and the ids end up in ``result.checkpoints``. When
the firmware waits, ``f`` runs while it waits, and the firmware carries on
only once ``f`` has returned:

.. code-block:: text

   firmware                            test (on_checkpoint)
   --------                            --------------------
   pastel_checkpoint(1, 1)  ───────▶   on_checkpoint(1) runs: drive pins,
     ...waits...                       read and write memory, check things
                            ◀───────   on_checkpoint(1) returns
   carries on

If ``f`` raises (a failed ``assert``, say), the test fails there. A checkpoint
that doesn't wait still calls ``f``, but the firmware may be well past it by
then.

Each example below is a real test, in
``packages/veerwolf/tests/test_checkpoints.py`` with its firmware in
``packages/veerwolf/tests/fw/``.

**1. Stimulus at a known point.** The firmware checks a "button" is up, waits
while the test presses it, then checks it sees the press:

.. code-block:: c

   PASTEL_CHECK((pastel_read32(GPIO) & BUTTON) == 0, "button pressed too early");
   pastel_checkpoint(1, 1);  // wait: the test presses the button, then lets us go
   PASTEL_CHECK(pastel_read32(GPIO) & BUTTON, "button press not seen");

.. code-block:: python

   def test_press_a_button_while_the_firmware_waits(harness, firmware):
       gpio = harness.iface("gpio", Gpio)

       def on_checkpoint(cp):
           if cp == 1:
               gpio.drive(BUTTON, BUTTON)

       firmware.run("fw/cp_stimulus.c", on_checkpoint=on_checkpoint)

The firmware does the checking here, so the test needs no assertion:
``firmware.run()`` fails the test if a ``PASTEL_CHECK`` fails.

**2. Inspecting the chip at a known point.** The firmware computes a value,
puts it on the output pins and waits while the test checks both the variable
and the pins. ``firmware.symbol(name)`` gives a firmware variable's address:

.. code-block:: c

   volatile uint32_t total;

   int test_main(void) {
     for (uint32_t i = 1; i <= 10; i++) total += i;
     pastel_write32(GPIO, total);
     pastel_checkpoint(1, 1);
     return 0;
   }

.. code-block:: python

   def on_checkpoint(cp):
       with harness.ocd.halted():
           assert harness.memory().read32(firmware.symbol("total")) == 55
       assert gpio.sample() & 0xFFFF == 55

``harness.memory()`` reaches the variable wherever it lives: through the
backdoor if there is one for that memory, otherwise through on-chip debug.
Some memories can only be reached through debug while the CPU is halted, such
as VeeR's DCCM at the ``ccm`` code location. ``harness.ocd.halted()`` halts the
CPU for the block and resumes it afterwards, which is harmless while the
firmware waits.

**3. Progress markers.** Checkpoints that don't wait record how far the
firmware got, for checking afterwards:

.. code-block:: python

   result = firmware.run("fw/cp_progress.c")
   assert result.checkpoints == [10, 20, 30]

**4. A conversation.** One step per checkpoint, keyed on the id. At each step
the test checks the firmware's answer to the last command and gives it the
next one, through two firmware variables:

.. code-block:: c

   volatile uint32_t command, answer;

   int test_main(void) {
     for (uint32_t step = 1; step <= 3; step++) {
       pastel_checkpoint(step, 1);   // wait for the test's command
       answer = command * command;
     }
     pastel_checkpoint(4, 1);        // let the test check the last answer
     return 0;
   }

.. code-block:: python

   def on_checkpoint(cp):
       with harness.ocd.halted():
           mem = harness.memory()
           if cp > 1:   # the firmware has answered the previous command
               sent = cp - 1 + 10
               assert mem.read32(firmware.symbol("answer")) == sent * sent
           if cp <= 3:
               mem.write32(firmware.symbol("command"), cp + 10)

**5. Sending the firmware data.** ``firmware.send(data)`` (up to 255 bytes)
and ``firmware.send_u32(value)``, called from ``on_checkpoint``, send data the
firmware takes with ``pastel_receive()`` and ``pastel_receive_u32()``. It needs
no firmware variables, and works where the firmware isn't halted for debug
access:

.. code-block:: c

   int test_main(void) {
     pastel_checkpoint(1, 1);                 // the test sends its parameters
     uint32_t n_samples = pastel_receive_u32();
     ...
   }

.. code-block:: python

   def on_checkpoint(cp):
       if cp == 1:
           firmware.send_u32(16)

Data sent while the firmware waits at a checkpoint is kept until it asks for
it, and messages arrive in the order sent.

Things to know:

- Ids are yours to choose, and may repeat. A marker and a waiting checkpoint
  with the same id don't interfere.
- A waiting checkpoint blocks the firmware's main code. Interrupt handlers
  still run.
- Firmware time keeps passing in the simulation while the test acts, except
  inside ``harness.ocd.halted()``.

Build options
~~~~~~~~~~~~~

``firmware.build()`` and ``firmware.run()`` accept several sources, plus
``include_dirs`` (relative to the test file, like the sources), ``defines``
(strings such as ``"N_SAMPLES=16"``) and ``stack_size``. Headers the device
pack provides for every test, such as the chip's register map, come from the
variant's ``fw_build`` hook (see :doc:`firmware`), so tests don't name them.

Firmware is built from scratch every time a test builds or runs it, into the
test's run directory, so a test never runs stale code; see
:ref:`how-firmware-is-built`.

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
temporary directory, its *run directory*.

What a run shows
~~~~~~~~~~~~~~~~

PASTEL logs each step of a test, and turns on pytest's live logging, so you
see them as they happen. With ``-v``, each test's lines sit under its name,
with its result after them:

.. code-block:: text

   src/soc_pastel/conformance/test_firmware.py::test_hello
   -------------------------------- live log call ---------------------------------
   INFO    built hello-ram.elf in 0.3 s
   INFO    simulator started; run directory /tmp/pytest-of-me/pytest-7/test_hello0
   INFO    simulator connected after 0.4 s
   INFO    OpenOCD ready (riscv.cpu) after 1.2 s
   INFO    loaded hello-ram.elf (1832 bytes via backdoor) in 0.0 s
   INFO    running hello-ram.elf from 0x0
   INFO    fw: Hello from veerwolf (sim)
   INFO    checkpoint 1 (waiting)
   INFO    firmware finished: test_main returned 0
   PASSED                                                                   [ 50%]

Model builds, warnings and errors appear the same way. This is pytest's own
live logging. If your configuration already sets it (``log_cli``,
``--log-cli-level``, ``log_cli_format``), PASTEL leaves your settings alone,
and ``--pastel-quiet`` stops PASTEL turning it on at all.

A failing test's report gets a **PASTEL: where to look** section: the test's
run directory, the logs in it, and the model's build log. The end of the run
lists the run directories of every failing test, and each test's waveform if
``--waves`` was given. To see failures as they happen rather than at the end,
add the ``pytest-instafail`` plugin and pass ``--instafail``.

Under pytest-xdist (``-n``), workers can't show live logs, but the failure
sections and the summary still appear.
