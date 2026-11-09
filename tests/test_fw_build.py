from soc_pastel.core import Access, CodeLocation, MemoryRegion, RegionKind, Variant
from soc_pastel.fw.build import linker_script, memory_map_header

VARIANT = Variant(
    name="v",
    regions=(
        MemoryRegion(name="ram", base=0x0, size=0x10000),
        MemoryRegion(name="iccm", base=0xEE000000, size=0x10000, access=Access.X),
        MemoryRegion(name="dccm", base=0xF0040000, size=0x10000, access=Access.RW),
        MemoryRegion(name="io", base=0x80001000, size=0x1000, kind=RegionKind.MMIO),
    ),
    code_locations=(
        CodeLocation(name="ram", text="ram", data="ram"),
        CodeLocation(name="ccm", text="iccm", data="dccm", rtt="ram"),
    ),
)


def test_memory_map_header_and_veer_mrac():
    from soc_pastel.core import CpuSpec, Device, InterfaceBinding

    dev = Device(
        name="d",
        cpu=CpuSpec(family="veer_el2", isa="rv32imc", abi="ilp32", reset_vector=0),
        variants={"v": VARIANT},
        default_variant="v",
        interfaces={
            "on_chip_debug": InterfaceBinding(type="on_chip_debug", impls={"sim": {"impl": "x"}})
        },
    )
    h = memory_map_header(dev, VARIANT)
    assert "#define PASTEL_DCCM_BASE 0xf0040000u" in h
    assert "#define PASTEL_VEER_MRAC 0x00020000" in h  # only region 8 (io) has side effects


def test_linker_script_roles():
    ram = linker_script(VARIANT, VARIANT.code_location("ram"), 2048)
    assert 'REGION_ALIAS("TEXT", RAM);' in ram and "RAM (rwx)" in ram
    ccm = linker_script(VARIANT, VARIANT.code_location("ccm"), 2048)
    assert 'REGION_ALIAS("TEXT", ICCM);' in ccm and "ICCM (rx)" in ccm
    assert 'REGION_ALIAS("DATA", DCCM);' in ccm and 'REGION_ALIAS("RTT", RAM);' in ccm
    assert ".rodata" in ccm and "> DATA" in ccm
