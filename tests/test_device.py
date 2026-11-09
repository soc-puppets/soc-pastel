import pytest

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
from soc_pastel.core.errors import ConfigurationError

CPU = CpuSpec(family="veer_el2", isa="rv32imc", abi="ilp32", reset_vector=0)
OCD = InterfaceBinding(type="on_chip_debug", impls={"sim": {"impl": "openocd"}})


def variant(**kw):
    defaults = dict(
        name="v",
        regions=(MemoryRegion(name="ram", base=0, size=0x1000),),
        code_locations=(CodeLocation(name="ram", text="ram", data="ram"),),
    )
    defaults.update(kw)
    return Variant(**defaults)


def device(**kw):
    defaults = dict(
        name="d",
        cpu=CPU,
        variants={"v": variant()},
        default_variant="v",
        interfaces={"on_chip_debug": OCD},
    )
    defaults.update(kw)
    return Device(**defaults)


def test_minimal_device_is_valid():
    assert device().variant().region("ram").size == 0x1000


def test_mmio_defaults_to_side_effects():
    assert MemoryRegion(name="io", base=0, size=4, kind=RegionKind.MMIO).side_effects
    assert not MemoryRegion(name="m", base=0, size=4).side_effects


def test_on_chip_debug_is_mandatory():
    with pytest.raises(ConfigurationError, match="on_chip_debug"):
        device(interfaces={})


def test_overlapping_regions_rejected():
    regions = (
        MemoryRegion(name="a", base=0, size=0x100),
        MemoryRegion(name="b", base=0x80, size=4),
    )
    with pytest.raises(ConfigurationError, match="overlap"):
        device(variants={"v": variant(regions=regions)})


def test_code_location_needs_executable_text():
    regions = (MemoryRegion(name="d", base=0, size=0x100, access=Access.RW),)
    locs = (CodeLocation(name="x", text="d", data="d"),)
    with pytest.raises(ConfigurationError, match="not executable"):
        device(variants={"v": variant(regions=regions, code_locations=locs)})


def test_code_location_needs_known_region():
    locs = (CodeLocation(name="x", text="nope", data="ram"),)
    with pytest.raises(ConfigurationError, match="no region"):
        device(variants={"v": variant(code_locations=locs)})


def test_binding_needs_impl():
    bad = InterfaceBinding(type="spi", impls={"sim": {"mode": 0}})
    with pytest.raises(ConfigurationError, match="impl"):
        device(interfaces={"on_chip_debug": OCD, "spi0": bad})


def test_binding_resolution_most_specific_first():
    b = InterfaceBinding(
        type="spi",
        impls={"sim": {"impl": "a"}, "verilator": {"impl": "b"}, "special": {"impl": "c"}},
    )
    assert b.resolve(variant="x", platform="xcelium", kind="sim")["impl"] == "a"
    assert b.resolve(variant="x", platform="verilator", kind="sim")["impl"] == "b"
    assert b.resolve(variant="special", platform="verilator", kind="sim")["impl"] == "c"
    assert b.resolve(variant="x", platform="board", kind="fpga") is None


def test_variant_selection():
    sim, fpga = variant(name="sim", platforms={"sim"}), variant(name="fpga", platforms={"fpga"})
    d = device(variants={"sim": sim, "fpga": fpga}, default_variant="sim")
    assert d.variant_for(platform="arty", kind="fpga").name == "fpga"
    assert d.variant_for(platform="verilator", kind="sim").name == "sim"
    assert d.variant_for(platform="verilator", kind="sim", requested="fpga").name == "fpga"
    assert d.variant_for(platform="bench", kind="silicon").name == "sim"  # default


def test_ambiguous_variant_needs_explicit_choice():
    a, b = variant(name="a", platforms={"fpga"}), variant(name="b", platforms={"fpga"})
    d = device(variants={"a": a, "b": b}, default_variant="a")
    with pytest.raises(ConfigurationError, match="--variant"):
        d.variant_for(platform="arty", kind="fpga")
