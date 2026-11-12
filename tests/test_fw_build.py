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


# --- The variant's fw_build hook, and what the firmware cache key covers ----------------

import shutil  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402

import pytest  # noqa: E402

from soc_pastel.core import ConfigurationError  # noqa: E402
from soc_pastel.fw.build import (  # noqa: E402
    FirmwareOptions,
    Toolchain,
    build_firmware,
    variant_options,
)


def device_with(variant):
    from soc_pastel.core import CpuSpec, Device, InterfaceBinding

    return Device(
        name="d",
        cpu=CpuSpec(family="veer_el2", isa="rv32imc_zicsr_zifencei", abi="ilp32", reset_vector=0),
        variants={variant.name: variant},
        default_variant=variant.name,
        interfaces={
            "on_chip_debug": InterfaceBinding(type="on_chip_debug", impls={"sim": {"impl": "x"}})
        },
    )


@pytest.fixture
def hook_variant(tmp_path, monkeypatch):
    """A variant whose fw_build hook adds tmp_path/chip_inc and a define."""
    inc = tmp_path / "chip_inc"
    (inc / "chip").mkdir(parents=True)
    (inc / "chip" / "regs.h").write_text("#define CHIP_REG_GPIO 0x80001010u\n")
    module = types.ModuleType("pastel_test_fw_hook")
    module.fw_build = lambda variant: FirmwareOptions(include_dirs=(inc,), defines=("CHIP_SIM=1",))
    module.not_options = lambda variant: {"include_dirs": [inc]}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    params = {**VARIANT.params, "fw_build": "pastel_test_fw_hook:fw_build"}
    return Variant(
        name="v", regions=VARIANT.regions, code_locations=VARIANT.code_locations, params=params
    )


def test_variant_options_from_the_hook(hook_variant, tmp_path):
    assert variant_options(VARIANT) == FirmwareOptions()  # no hook
    opts = variant_options(hook_variant)
    assert opts.include_dirs == (tmp_path / "chip_inc",) and opts.defines == ("CHIP_SIM=1",)


def test_bad_hooks_are_reported(hook_variant):
    def with_hook(ref):
        return Variant(
            name="v",
            regions=VARIANT.regions,
            code_locations=VARIANT.code_locations,
            params={"fw_build": ref},
        )

    with pytest.raises(ConfigurationError, match="module:function"):
        variant_options(with_hook("no_colon"))
    with pytest.raises(ConfigurationError, match="not FirmwareOptions"):
        variant_options(with_hook("pastel_test_fw_hook:not_options"))


def _have_toolchain() -> bool:
    try:
        return shutil.which(Toolchain().tool("gcc")) is not None
    except ConfigurationError:
        return False


needs_toolchain = pytest.mark.skipif(not _have_toolchain(), reason="RISC-V toolchain not installed")

FW = """
#include "pastel/pastel.h"
#include "chip/regs.h"
#ifndef CHIP_SIM
#error the variant define is missing
#endif
int test_main(void) { return pastel_read32(CHIP_REG_GPIO) == 0 ? 0 : 1; }
"""


@needs_toolchain
def test_hook_include_dirs_and_defines_reach_the_compiler(hook_variant, tmp_path):
    src = tmp_path / "t.c"
    src.write_text(FW)
    dev = device_with(hook_variant)
    elf = build_firmware(
        dev, hook_variant, hook_variant.code_location("ram"), [src], tmp_path / "o"
    )
    assert elf.is_file()
    log = (elf.parent / "build.log").read_text()
    assert log.index("/include ") < log.index(f"-I{tmp_path / 'chip_inc'}")  # PASTEL's first
    assert "-DCHIP_SIM=1" in log


@needs_toolchain
def test_changing_an_included_header_rebuilds(hook_variant, tmp_path):
    src = tmp_path / "t.c"
    src.write_text(FW)
    dev, loc = device_with(hook_variant), hook_variant.code_location("ram")
    first = build_firmware(dev, hook_variant, loc, [src], tmp_path / "o")
    assert build_firmware(dev, hook_variant, loc, [src], tmp_path / "o") == first  # cached
    (tmp_path / "chip_inc" / "chip" / "regs.h").write_text("#define CHIP_REG_GPIO 0x80001018u\n")
    assert build_firmware(dev, hook_variant, loc, [src], tmp_path / "o") != first
