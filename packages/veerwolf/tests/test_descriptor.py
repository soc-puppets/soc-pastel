from soc_pastel.core import registry
from soc_pastel_veerwolf import HDL_ROOT, VEERWOLF


def test_registered_through_entry_point():
    assert registry.device("veerwolf") is VEERWOLF
    assert "veerwolf" in registry.hdl_roots()
    assert HDL_ROOT.is_dir()


def test_sim_has_ccm_fpga_does_not():
    assert [c.name for c in VEERWOLF.variant("sim").code_locations] == ["ram", "ccm"]
    assert [c.name for c in VEERWOLF.variant("fpga").code_locations] == ["ram"]


def test_variant_chosen_by_platform_kind():
    assert VEERWOLF.variant_for(platform="verilator", kind="sim").name == "sim"
    assert VEERWOLF.variant_for(platform="arty", kind="fpga").name == "fpga"


def test_openocd_bound_everywhere():
    b = VEERWOLF.interfaces["on_chip_debug"]
    for kind in ("sim", "fpga"):
        assert b.resolve(variant="x", platform="x", kind=kind)["impl"] == "openocd"
