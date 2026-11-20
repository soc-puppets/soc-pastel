"""RISC-V register names.

The integer registers' ABI names (``a0``, ``sp``, ...), for debug drivers whose
probe software only knows ``x0``-``x31``. The names are fixed by the RISC-V
psABI specification, "Integer Register Convention"
(https://github.com/riscv-non-isa/riscv-elf-psabi-doc). CSR names come from the
probe software, not from here.
"""

from __future__ import annotations

#: ABI names of ``x0``-``x31``, in order.
ABI_NAMES: tuple[str, ...] = (
    "zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2",
    "s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5",
    "a6", "a7", "s2", "s3", "s4", "s5", "s6", "s7",
    "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6",
)  # fmt: skip
#: Other names for some integer registers: ``fp`` is ``s0`` (``x8``).
ALIASES: dict[str, int] = {"fp": 8}


def gpr_number(name: str) -> int | None:
    """The number of the integer register called ``name`` (``x10``, ``a0``, ``fp``), or None."""
    name = name.lower()
    if name.startswith("x") and name[1:].isdigit() and int(name[1:]) < 32:
        return int(name[1:])
    if name in ALIASES:
        return ALIASES[name]
    try:
        return ABI_NAMES.index(name)
    except ValueError:
        return None
