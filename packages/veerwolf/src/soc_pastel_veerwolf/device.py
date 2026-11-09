"""Device descriptor for VeeRwolf with VeeR EL2.

Sources: the VeeRwolf README and RTL, and the PASTEL feasibility spike
against upstream commit 9a91980 (VeeRwolf 0.7.5, VeeR EL2 1.4).
"""

from soc_pastel.core.device import (
    Access,
    CodeLocation,
    CpuSpec,
    Device,
    InterfaceBinding,
    MemoryRegion,
    RegionKind,
    Variant,
)

# --- Regions common to every build ------------------------------------------------

# The boot ROM is wired to the instruction port only: execute-only, and not
# reachable by debug memory access. PASTEL never relies on it: reset-halt stops
# the core at the reset vector before the ROM executes.
BOOTROM = MemoryRegion(
    name="bootrom",
    base=0x8000_0000,
    size=0x1000,
    kind=RegionKind.ROM,
    access=Access.X,
    debug_access=False,
)
# System controller: version, sim_print/sim_exit, GPIO, timers, simple_spi.
SYSCON = MemoryRegion(
    name="syscon", base=0x8000_1000, size=0x1000, kind=RegionKind.MMIO, access=Access.RW
)
UART = MemoryRegion(
    name="uart", base=0x8000_2000, size=0x1000, kind=RegionKind.MMIO, access=Access.RW
)
# VeeR programmable interrupt controller (RV_PIC_SIZE = 32 KiB).
PIC = MemoryRegion(
    name="pic", base=0xF00C_0000, size=0x8000, kind=RegionKind.MMIO, access=Access.RW
)

# --- Variants ---------------------------------------------------------------------------

# Simulation build: VeeRwolf's default EL2 configuration, which enables 64 KiB
# ICCM and DCCM. RAM is a model in PASTEL's own testbench
# (hw/pastel_veerwolf_tb.sv); keep its RAM_SIZE in step with "ram".
SIM = Variant(
    name="sim",
    platforms={"sim"},
    regions=(
        MemoryRegion(name="ram", base=0x0000_0000, size=0x10_0000, backdoor="ram"),
        # No backdoor into the CCMs yet: VeeR stores them with ECC, so a backdoor
        # must compute check bits. Until then they are loaded over debug.
        MemoryRegion(name="iccm", base=0xEE00_0000, size=0x1_0000, access=Access.X),
        MemoryRegion(name="dccm", base=0xF004_0000, size=0x1_0000, access=Access.RW),
        BOOTROM,
        SYSCON,
        UART,
        PIC,
    ),
    code_locations=(
        CodeLocation(name="ram", text="ram", data="ram"),
        # RTT stays in system RAM: VeeR cannot read the DCCM over debug while
        # the CPU runs, and the backdoor reaches RAM.
        CodeLocation(name="ccm", text="iccm", data="dccm", rtt="ram"),
    ),
    params={"sim_sources": "soc_pastel_veerwolf.sim:sim_sources"},
)

# FPGA builds (Arty A7, Basys 3): 64 KiB on-chip RAM, ICCM and DCCM disabled
# by VeeRwolf's FPGA EL2 configurations. (Nexys A7 uses DDR2; not covered yet.)
FPGA = Variant(
    name="fpga",
    platforms={"fpga"},
    regions=(
        MemoryRegion(name="ram", base=0x0000_0000, size=0x1_0000),
        BOOTROM,
        SYSCON,
        UART,
        PIC,
    ),
    code_locations=(CodeLocation(name="ram", text="ram", data="ram"),),
    params={"fusesoc_flags": ("cpu_el2",), "boards": ("arty_a7", "basys3")},
)

# --- On-chip debug ------------------------------------------------------------------------

_SIM_CHAIN = """\
jtag newtap riscv cpu -irlen 5 -expected-id 0x00000001
set _TARGETNAME riscv.cpu
target create $_TARGETNAME riscv -chain-position $_TARGETNAME
"""

# On the FPGA boards, debug is tunnelled through the FPGA's own JTAG TAP
# (Xilinx BSCAN) and reached through the board's USB FTDI interface.
_DIGILENT_FTDI = """\
adapter driver ftdi
ftdi device_desc "Digilent USB Device"
ftdi vid_pid 0x0403 0x6010
ftdi channel 0
ftdi layout_init 0x0088 0x008b
reset_config none
adapter speed 10000
transport select jtag
"""

_FPGA_CHAIN = """\
jtag newtap riscv cpu -irlen 6 -expected-id 0x03631093 -ignore-version
set _TARGETNAME riscv.cpu
target create $_TARGETNAME riscv -chain-position $_TARGETNAME
riscv set_ir idcode 0x9
riscv set_ir dmi 0x22
riscv set_ir dtmcs 0x23
"""

# --- Device -----------------------------------------------------------------------------

VEERWOLF = Device(
    name="veerwolf",
    cpu=CpuSpec(
        family="veer_el2",
        isa="rv32imc_zicsr_zifencei",
        abi="ilp32",
        reset_vector=0x8000_0000,
        params={"pic_base": 0xF00C_0000},
    ),
    variants={"sim": SIM, "fpga": FPGA},
    default_variant="sim",
    interfaces={
        "on_chip_debug": InterfaceBinding(
            type="on_chip_debug",
            impls={
                "sim": {
                    "impl": "openocd",
                    "transport": "remote_bitbang",
                    "cpu": "veer",
                    "chain": _SIM_CHAIN,
                },
                "fpga": {
                    "impl": "openocd",
                    "transport": _DIGILENT_FTDI,
                    "cpu": "veer",
                    "chain": _FPGA_CHAIN,
                },
            },
        ),
        # GPIO, UART and SPI bindings arrive with their interface packs (Phase 4).
    },
    # VeeRwolf's data/veerwolf.rdl covers only part of the syscon; a register
    # model follows once it is extended to GPIO, timers, SPI and UART.
    register_model=None,
    hdl="soc-puppets:pastel:veerwolf_tb",
)
