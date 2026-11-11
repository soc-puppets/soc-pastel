"""What the Verilator model's identity covers, and how sim_sources hooks are called."""

from pathlib import Path

import pytest

from soc_pastel.core import CodeLocation, MemoryRegion, Variant
from soc_pastel.sim.sources import SimSources, load_sim_sources
from soc_pastel.sim.verilator import VerilatorImage

SOURCES = SimSources(top="tb", args=())


def key(tmp_path, sources=SOURCES, verilator="verilator", **kw):
    return VerilatorImage(sources, tmp_path, verilator, 1, **kw).key


def test_key_covers_extra_args_from_option_and_sources(tmp_path):
    base = key(tmp_path)
    assert key(tmp_path) == base
    assert key(tmp_path, extra_args=("-CFLAGS", "-fcoroutines")) != base
    tooled = SimSources(top="tb", args=(), tool_args={"verilator": ("-CFLAGS", "-fcoroutines")})
    assert key(tmp_path, tooled) == key(tmp_path, extra_args=("-CFLAGS", "-fcoroutines"))
    other_tool = SimSources(top="tb", args=(), tool_args={"xcelium": ("-access", "+r")})
    assert key(tmp_path, other_tool) == base  # another simulator's args don't apply


def test_key_covers_verilator_and_compiler_environment(tmp_path, monkeypatch):
    base = key(tmp_path)
    assert key(tmp_path, verilator="/opt/verilator/v5.042/bin/verilator") != base
    monkeypatch.setenv("CXX", "/opt/gcc-12/bin/g++")
    assert key(tmp_path) != base


def test_extra_args_go_after_pastel_options_and_before_sources(tmp_path):
    sources = SimSources(top="tb", args=("/src/tb.sv",), tool_args={"verilator": ("--timing",)})
    extra = ("-LDFLAGS", "-Wl,-rpath,/x")
    cmd = VerilatorImage(sources, tmp_path, "verilator", 1, extra_args=extra).command()
    at = cmd.index("--timing")
    assert cmd[at : at + 3] == ["--timing", *extra]
    assert cmd.index("-Wno-fatal") < at < cmd.index("/src/tb.sv")


def variant_with(hook: str) -> Variant:
    return Variant(
        name="v",
        regions=(MemoryRegion(name="ram", base=0, size=0x100),),
        code_locations=(CodeLocation(name="ram", text="ram", data="ram"),),
        params={"sim_sources": hook},
    )


def legacy_hook(workdir: Path) -> SimSources:
    return SimSources(top="legacy", args=())


def platform_hook(workdir: Path, platform: str = "verilator") -> SimSources:
    return SimSources(top=f"tb_{platform}", args=())


def kwargs_hook(workdir: Path, **context) -> SimSources:
    return SimSources(top=f"tb_{context['platform']}", args=())


@pytest.mark.parametrize(
    "hook, top",
    [("legacy_hook", "legacy"), ("platform_hook", "tb_xcelium"), ("kwargs_hook", "tb_xcelium")],
)
def test_hooks_are_told_the_platform_if_they_ask(tmp_path, hook, top):
    v = variant_with(f"{__name__}:{hook}")
    assert load_sim_sources(v, tmp_path, platform="xcelium").top == top
