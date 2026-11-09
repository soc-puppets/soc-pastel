"""Simulation sources for VeeRwolf.

VeeRwolf's FuseSoC core is not designed to be used as a dependency (it has no
default target, and the VeeR configuration is generated per target), so this
lets FuseSoC prepare VeeRwolf's own ``lint`` target, which generates the EL2
configuration and resolves every dependency, then reuses the resulting
Verilator command file with the PASTEL testbench added.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from soc_pastel.core.errors import ConfigurationError
from soc_pastel.sim.sources import SimSources

from . import HDL_ROOT

#: Upstream VeeRwolf commit this pack is tested against.
VEERWOLF_COMMIT = "9a91980562fcaadd748404af05d2a23361632378"
VEERWOLF_URL = "https://github.com/chipsalliance/VeeRwolf"
FUSESOC_CORES_URL = "https://github.com/fusesoc/fusesoc-cores"


def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        output = result.stdout[-2000:] + result.stderr[-2000:]
        raise ConfigurationError(f"command failed in {cwd}: {shlex.join(cmd)}\n{output}")


def _workspace(workdir: Path) -> Path:
    """A FuseSoC workspace with VeeRwolf (pinned) and fusesoc-cores."""
    ws = Path(os.environ.get("PASTEL_VEERWOLF_WORKSPACE", workdir / "fusesoc"))
    ws.mkdir(parents=True, exist_ok=True)
    libs = ws / "fusesoc_libraries"
    if not (libs / "fusesoc-cores").is_dir():
        _run(["fusesoc", "library", "add", "fusesoc-cores", FUSESOC_CORES_URL], ws)
    vw = libs / "veerwolf"
    if not vw.is_dir():
        _run(["fusesoc", "library", "add", "veerwolf", VEERWOLF_URL], ws)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=vw, capture_output=True, text=True
    ).stdout.strip()
    if head != VEERWOLF_COMMIT:
        _run(["git", "fetch", "--quiet", "origin", VEERWOLF_COMMIT], vw)
        _run(["git", "checkout", "--quiet", VEERWOLF_COMMIT], vw)
    return ws


def _absolute(arg: str, base: Path) -> str:
    if arg.startswith("+incdir+"):
        return "+incdir+" + str(base / arg[len("+incdir+") :])
    if arg.startswith(("-", "+")):
        return arg
    return str(base / arg)


def sim_sources(workdir: Path) -> SimSources:
    """Return the Verilator sources for the PASTEL VeeRwolf testbench."""
    build_root = workdir / "veerwolf-lint"
    marker = build_root / "pastel-setup-done"
    if not marker.is_file() or marker.read_text() != VEERWOLF_COMMIT:
        ws = _workspace(workdir)
        _run(
            [
                "fusesoc",
                "run",
                "--setup",
                "--target=lint",
                "--flag=cpu_el2",
                f"--build-root={build_root}",
                "veerwolf",
            ],
            ws,
        )
        marker.write_text(VEERWOLF_COMMIT)
    ws = Path(os.environ.get("PASTEL_VEERWOLF_WORKSPACE", workdir / "fusesoc"))
    lint = next(build_root.glob("veerwolf_*/lint-verilator"))
    vc = next(lint.glob("*.vc"))
    args: list[str] = []
    for line in vc.read_text().splitlines():
        tokens = shlex.split(line)
        if not tokens or tokens[0] in ("--Mdir", "--top-module", "--exe", "-CFLAGS"):
            continue
        args += [_absolute(t, lint) for t in tokens]
    vw = ws / "fusesoc_libraries" / "veerwolf"
    args += [
        str(vw / "rtl" / "axi_ram.v"),
        str(HDL_ROOT / "pastel_veerwolf_tb.sv"),
    ]
    return SimSources(top="pastel_veerwolf_tb", args=tuple(args), key=VEERWOLF_COMMIT)
