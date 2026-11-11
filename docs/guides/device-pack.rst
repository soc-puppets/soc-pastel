Writing a device pack
=====================

A **device pack** is everything PASTEL needs to know about one chip: its
descriptor, its simulation testbench and how to build it, and anything
chip-specific such as HALs or a register model. The core never contains
chip-specific code.

This guide brings up a new chip step by step, using the VeeRwolf pack
(``packages/veerwolf`` in the repository) as the worked example. Each step ends
with a **checkpoint**: something you can run to confirm that step works
before moving on. Bring-up problems are much easier to find one layer at a
time.

.. list-table::
   :header-rows: 1

   * - Step
     - You will have
     - Checkpoint
   * - :ref:`1. Package <step-1-create-the-package>`
     - An installable pack PASTEL discovers
     - ``pastel list``
   * - :ref:`2. Descriptor <step-2-write-the-descriptor>`
     - The chip's memory map and interfaces
     - ``pastel describe``
   * - :ref:`3. Testbench <step-3-write-the-simulation-testbench>`
     - A testbench PASTEL can drive
     - Builds and connects
   * - :ref:`4. Sources hook <step-4-tell-pastel-how-to-build-it>`
     - PASTEL can build the model
     - Debug tests pass
   * - :ref:`5. Conformance <step-5-pass-the-conformance-suite>`
     - A working device
     - Whole suite passes

.. _step-1-create-the-package:

Step 1: create the package
--------------------------

A device pack is an ordinary Python package that registers its descriptor and
its HDL through entry points:

.. code-block:: text

   my-chip-pastel/
   ├── pyproject.toml
   └── src/my_chip_pastel/
       ├── __init__.py      # exports HDL_ROOT
       ├── device.py        # the Device descriptor
       ├── sim.py           # the sim_sources hook (step 4)
       └── hw/              # the PASTEL testbench (step 3)

.. code-block:: toml

   [project]
   name = "my-chip-pastel"
   version = "0.1.0"
   dependencies = ["soc-pastel"]

   [project.entry-points."soc_pastel.devices"]
   my_chip = "my_chip_pastel.device:MY_CHIP"

   [project.entry-points."soc_pastel.hdl"]
   my_chip = "my_chip_pastel:HDL_ROOT"

.. code-block:: python

   # src/my_chip_pastel/__init__.py
   from pathlib import Path

   HDL_ROOT: Path = Path(__file__).parent / "hw"

A pack for a proprietary chip lives in that chip's private repository and is
installed in development mode; it never needs publishing:

.. code-block:: shell

   pip install -e path/to/my-chip-pastel

.. admonition:: Checkpoint

   ``pastel list`` shows your device. (Until step 2 is done it will fail to load,
   with an error naming the problem.)

.. _step-2-write-the-descriptor:

Step 2: write the descriptor
----------------------------

The descriptor is a :py:class:`~soc_pastel.core.device.Device`. It is checked
when it loads, and every error names the region, code location or binding at
fault.

The CPU
~~~~~~~

.. code-block:: python

   from soc_pastel.core.device import CpuSpec

   CPU = CpuSpec(
       family="veer_el2",  # selects the firmware port
       isa="rv32imc_zicsr_zifencei",  # -march for test firmware
       abi="ilp32",  # -mabi
       reset_vector=0x8000_0000,
   )

``family`` must name a firmware port PASTEL provides (currently ``veer_el2``).
Add extensions your firmware may use to ``isa``, e.g.
``rv32imc_zicsr_zifencei_zba_zbb_zbc_zbs`` for a core with bit manipulation.

Memory regions
~~~~~~~~~~~~~~

List every region of the address map firmware or tests touch:

.. code-block:: python

   from soc_pastel.core.device import Access, MemoryRegion, RegionKind

   RAM = MemoryRegion(name="ram", base=0x0000_0000, size=0x10_0000, backdoor="ram")
   ICCM = MemoryRegion(name="iccm", base=0xEE00_0000, size=0x1_0000, access=Access.X)
   UART = MemoryRegion(
       name="uart", base=0x8000_2000, size=0x1000, kind=RegionKind.MMIO, access=Access.RW
   )
   BOOTROM = MemoryRegion(
       name="bootrom",
       base=0x8000_0000,
       size=0x1000,
       kind=RegionKind.ROM,
       access=Access.X,
       debug_access=False,
   )

.. list-table::
   :header-rows: 1

   * - Field
     - Meaning
   * - ``kind``
     - ``RAM``, ``ROM`` or ``MMIO``. MMIO regions default to ``side_effects=True``.
   * - ``access``
     - What the CPU may do. Execute-only memories (an ICCM) are ``Access.X``.
   * - ``side_effects``
     - I/O semantics: never cached or speculated. The VeeR port turns these into MRAC settings, so include every peripheral region.
   * - ``backdoor``
     - Name of the testbench backdoor for this region (step 3), or omit it. Regions with a backdoor load in milliseconds in simulation; without one, over debug, in seconds.
   * - ``debug_access``
     - Whether on-chip debug can read and write it. Set ``False`` for memories not wired to the debug path (VeeRwolf's boot ROM).

Regions may not overlap.

Code locations
~~~~~~~~~~~~~~

A :py:class:`~soc_pastel.core.device.CodeLocation` is somewhere test firmware
can run from. Tests can run at each location with ``--code-location``:

.. code-block:: python

   from soc_pastel.core.device import CodeLocation

   LOCATIONS = (
       CodeLocation(name="ram", text="ram", data="ram"),
       CodeLocation(name="ccm", text="iccm", data="dccm", rtt="ram"),
   )

``text`` holds code and must be executable. ``data`` holds read-only data, data,
BSS and the stack, and must be readable and writable. Read-only data goes with
the data because text regions may be execute-only.

.. important::

   ``rtt`` (which defaults to ``data``) holds the RTT buffers, which the host reads
   **while the CPU runs**. It must be a region the host can reach then: one with
   a backdoor in simulation, and one on the system bus on hardware. VeeR cannot
   read its DCCM over debug while the CPU runs, which is why VeeRwolf's ``ccm``
   location puts RTT in RAM.

Variants
~~~~~~~~

A :py:class:`~soc_pastel.core.device.Variant` is one build of the chip. Most
chips start with one; add more when builds differ in their memory map:

.. code-block:: python

   from soc_pastel.core.device import Variant

   SIM = Variant(
       name="sim",
       platforms={"sim"},  # platform kinds or names it serves
       regions=(RAM, ICCM, DCCM, BOOTROM, SYSCON, UART, PIC),
       code_locations=LOCATIONS,
       params={"sim_sources": "my_chip_pastel.sim:sim_sources"},  # step 4
   )

PASTEL picks the variant whose ``platforms`` lists the platform's name, then
its kind (``sim``, ``fpga``, ``silicon``), then falls back to ``default_variant``.
``--variant`` overrides it. A variant can serve several kinds, e.g.
``platforms={"sim", "silicon"}`` when simulation runs the ASIC's memory map.

Interfaces: on-chip debug
~~~~~~~~~~~~~~~~~~~~~~~~~

Every device binds an ``on_chip_debug`` interface. Bindings are chosen per
platform, trying the variant name, then the platform name, then the platform
kind:

.. code-block:: python

   from soc_pastel.core.device import Device, InterfaceBinding

   SIM_CHAIN = """\
   jtag newtap riscv cpu -irlen 5 -expected-id 0x00000001
   set _TARGETNAME riscv.cpu
   target create $_TARGETNAME riscv -chain-position $_TARGETNAME
   """

   MY_CHIP = Device(
       name="my_chip",
       cpu=CPU,
       variants={"sim": SIM},
       default_variant="sim",
       interfaces={
           "on_chip_debug": InterfaceBinding(
               type="on_chip_debug",
               impls={
                   "sim": {
                       "impl": "openocd",
                       "transport": "remote_bitbang",
                       "cpu": "veer",
                       "chain": SIM_CHAIN,
                   },
               },
           ),
       },
   )

The OpenOCD configuration is assembled from three parts:

``transport``
   How JTAG reaches the chip. In simulation always ``remote_bitbang``; PASTEL
   runs OpenOCD beside the simulator and points it at the testbench's JTAG
   socket.
``cpu``
   Debug-module settings for the CPU family. ``veer`` selects system-bus memory
   access with abstract access as a fallback, exposes VeeR's ``dmst`` CSR, and
   fences before resuming.
``chain``
   Tcl defining the JTAG TAP and creating ``$_TARGETNAME``. For VeeR's own TAP
   (``dmi_wrapper``/``rvjtag_tap``) the IR length is 5 and the IDCODE is the
   wrapper's ``jtag_id`` input with bit 0 set: ``jtag_id = 0`` gives
   ``0x00000001``. If you are unsure of the IDCODE, leave out ``-expected-id``
   until OpenOCD's log tells you what it found.

.. admonition:: Checkpoint

   ``pastel describe my_chip`` prints the memory map, code locations and bindings.
   Check every address against the chip's documentation now: everything later
   depends on it.

.. _step-3-write-the-simulation-testbench:

Step 3: write the simulation testbench
--------------------------------------

PASTEL needs a testbench top that wraps the chip with a few PASTEL modules.
VeeRwolf's is ``packages/veerwolf/src/soc_pastel_veerwolf/hw/pastel_veerwolf_tb.sv``.

Requirements
~~~~~~~~~~~~

1. **The top module is plain SystemVerilog with no ports**, as for any
   simulator, and generates its own clock. ``pastel_clock`` gives you one: 50 MHz
   by default (set its ``PERIOD_PS`` parameter, or ``+pastel_clk_period_ps=<n>``
   at run time), correct whatever timescale the design uses. Any other clock
   generator works too.
2. **Generate reset inside the testbench**, e.g. hold it for 16 cycles.
3. **Instantiate ``pastel_server`` exactly once.** It connects to the test
   process on the first clock edge and ends the simulation when asked.
4. **Connect ``pastel_jtag`` to the chip's JTAG TAP.** If the chip exposes
   JTAG pins, wire them directly. VeeRwolf's core exposes the debug module
   interface instead, so its testbench adds VeeR's ``dmi_wrapper`` between the
   two.
5. **Add a backdoor for each region with a ``backdoor`` name** (below).
6. **Don't let the CPU run anything harmful before PASTEL halts it.** PASTEL
   resets the chip and halts it at the reset vector over JTAG before loading
   code, so a boot ROM is never needed. If one exists, park it in a loop (as
   VeeRwolf's testbench does) or rely on reset-halt.

.. code-block:: systemverilog

   `include "pastel_backdoor.svh"

   module my_chip_tb;
     logic clk;
     pastel_clock u_clk (.clk(clk));

     logic [4:0] rst_count = '0;
     logic rst;
     always_ff @(posedge clk) if (!rst_count[4]) rst_count <= rst_count + 1'b1;
     assign rst = !rst_count[4];

     pastel_server u_pastel (.clk(clk));

     logic tck, tms, tdi, trst_n, tdo;
     pastel_jtag u_jtag (.clk(clk), .tck(tck), .tms(tms), .tdi(tdi),
                         .trst_n(trst_n), .tdo(tdo));

     my_chip dut (.clk(clk), .rst_n(!rst), .jtag_tck(tck), .jtag_tms(tms),
                  .jtag_tdi(tdi), .jtag_trst_n(trst_n), .jtag_tdo(tdo) /* ... */);

     my_chip_backdoor_ram u_backdoor_ram (.clk(clk));
   endmodule

Backdoors
~~~~~~~~~

A backdoor gives the host direct access to a memory array, in zero simulated
time. For a single array of little-endian words, one macro does it:

.. code-block:: systemverilog

   module my_chip_backdoor_ram (input logic clk);
     import pastel_ep_pkg::*;
     `PASTEL_BACKDOOR_MEM(clk, "ram", my_chip_tb.dut.u_ram.mem, 8)  // 8-byte words
   endmodule

The name (``"ram"``) must match the region's ``backdoor`` name. Offsets are bytes
from the region base.

For anything else (banked or interleaved RAMs, memories with ECC) use the
general form: define how to read and write one byte, and the macro does the
rest. This one serves a RAM split into two banks interleaved by 32-bit word:

.. code-block:: systemverilog

   module my_chip_backdoor_ram0 (input logic clk);
     import pastel_ep_pkg::*;

     function automatic byte pastel_bd_read(longint unsigned offset);
       bit          bank  = offset[2];       // word index modulo 2
       bit   [9:0]  index = offset[12:3];    // word index divided by 2
       int          lane  = int'(offset[1:0]);
       logic [31:0] w     = bank ? my_chip_tb.dut.bank1.mem[index]
                                 : my_chip_tb.dut.bank0.mem[index];
       return byte'(w >> (8 * lane));
     endfunction

     function automatic void pastel_bd_write(longint unsigned offset, byte value);
       bit         bank  = offset[2];
       bit   [9:0] index = offset[12:3];
       int         lane  = int'(offset[1:0]);
       if (bank) my_chip_tb.dut.bank1.mem[index][8 * lane +: 8] = value;
       else      my_chip_tb.dut.bank0.mem[index][8 * lane +: 8] = value;
     endfunction

     `PASTEL_BACKDOOR(clk, "ram0", 2 * 1024 * 4)   // region size in bytes
   endmodule

For a memory with ECC, recompute the check bits for the affected word in
``pastel_bd_write``. Use one backdoor per module. Verilator's ``-Wall`` reports
the deliberate blocking writes (``BLKSEQ``); PASTEL's build does not enable
those warnings.

.. _step-4-tell-pastel-how-to-build-it:

Step 4: tell PASTEL how to build it
-----------------------------------

The variant's ``sim_sources`` parameter names a function returning a
:py:class:`~soc_pastel.sim.sources.SimSources`: the testbench top and the
arguments that compile it. PASTEL adds its own HDL and C++, then builds with
Verilator.

If your flow already has a file list, the hook can be a few lines:

.. code-block:: python

   # src/my_chip_pastel/sim.py
   import os
   from pathlib import Path

   from soc_pastel.sim.sources import SimSources

   from . import HDL_ROOT

   def sim_sources(workdir: Path) -> SimSources:
       root = Path(os.environ["MY_CHIP_ROOT"])  # wherever the RTL lives
       args = []
       for line in (root / "rtl" / "files.f").read_text().splitlines():
           line = line.strip()
           if not line or line.startswith("//"):
               continue
           if line.startswith("+incdir+"):
               args.append("+incdir+" + str(root / line[len("+incdir+") :]))
           elif line.startswith(("-D", "+define+")):
               args.append(line)
           else:
               args.append(str(root / line))
       args.append(str(HDL_ROOT / "my_chip_tb.sv"))
       return SimSources(top="my_chip_tb", args=tuple(args))

Arguments are file paths (absolute), ``+incdir+<dir>``, ``-D<name>[=<value>]``,
and Verilator configuration files (``.vlt``). ``workdir`` is yours to use for any
set-up, such as generating files. VeeRwolf's hook uses it to let FuseSoC
generate the VeeR configuration and resolve dependencies; see
``soc_pastel_veerwolf/sim.py``.

The model is rebuilt only when a listed file's content changes (ignoring
comment-only lines, which generators often timestamp). ``key`` adds anything
else that should force a rebuild, such as an upstream commit.

.. admonition:: Checkpoint

   Run just the debug tests:

   .. code-block:: shell

      pytest --pyargs soc_pastel.conformance -k test_debug --device=my_chip --platform=verilator

   The first run builds the model; its log is
   ``.pastel/verilator/images/<top>-<hash>/build.log``. Then, in order:

   1. The simulator connects and its endpoints match the descriptor. A mismatch
      lists every disagreement, such as a backdoor name that differs.
   2. OpenOCD examines the CPU. Its log, ``openocd.log``, is in the test's pytest
      temporary directory; it shows the IDCODE found and the IR length.
   3. ``reset halt`` stops at ``reset_vector``.

   See :doc:`troubleshooting` for each failure.

.. _step-5-pass-the-conformance-suite:

Step 5: pass the conformance suite
----------------------------------

.. code-block:: shell

   pytest --pyargs soc_pastel.conformance --device=my_chip --platform=verilator
   pytest --pyargs soc_pastel.conformance --device=my_chip --platform=verilator --code-location=ccm

Bring it up in this order, because each file depends on the one before:

``test_debug.py``
   Reset-halt at the reset vector, register access, halt after resume.
``test_memory.py``
   Debug memory access to every RAM region with ``debug_access``, and agreement
   between backdoor and debug. Failures here usually mean a wrong region base
   or size, or a backdoor bound to the wrong array.
``test_firmware.py``
   Firmware at the selected code location: output over RTT, results, fault
   reporting, checkpoints and shared memory. A timeout waiting for RTT usually
   means the firmware never reached ``test_main``: check the reset vector, the
   code location's regions, and ``side_effects`` on peripheral regions.

Run it at every code location you declared.

Later: other platforms
----------------------

FPGA and silicon support is added to the same pack: a variant for each build
whose memory map differs, and on-chip debug bindings for the ``fpga`` and
``silicon`` kinds (or for particular boards, by variant name). Interfaces
other than on-chip debug (GPIO, SPI, UART) are bound the same way as their
interface packs become available.

Checklist
---------

-  The pack installs and ``pastel list`` shows the device.
-  ``pastel describe`` matches the chip's documented memory map.
-  Every peripheral region is ``MMIO`` (so it has ``side_effects``).
-  Each code location's RTT region is readable while the CPU runs.
-  The testbench top has one input, ``clk``, and one ``pastel_server``.
-  ``pastel_jtag`` reaches the CPU's TAP, and the ``chain`` Tcl matches it.
-  Each ``backdoor`` name in the descriptor has a backdoor module.
-  The conformance suite passes at every code location.
