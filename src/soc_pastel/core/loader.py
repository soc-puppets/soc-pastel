"""Loading ELF images into the target."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from .errors import ConfigurationError, TargetError
from .memory import MemoryInterface

if TYPE_CHECKING:
    from .harness import PlatformHarness


def memory_for(harness: PlatformHarness, addr: int, length: int) -> MemoryInterface:
    """The fastest memory interface that reaches ``[addr, addr + length)``."""
    mem = harness.memory()
    return mem if mem.can_access(addr, length) else harness.ocd.memory


def load_elf(harness: PlatformHarness, path: Path, *, verify: bool = False) -> int:
    """Write every loadable segment of an ELF into target memory; return the entry point.

    Each segment goes through the backdoor where one reaches it and through
    on-chip debug otherwise. The CPU must be halted. Zero-initialised data is
    not written; the firmware start-up code clears it.
    """
    with open(path, "rb") as f:
        elf = ELFFile(f)
        for seg in elf.iter_segments():
            if seg["p_type"] != "PT_LOAD" or seg["p_filesz"] == 0:
                continue
            addr, data = seg["p_paddr"], seg.data()
            mem = memory_for(harness, addr, len(data))
            mem.write(addr, data)
            if verify and mem.read(addr, len(data)) != data:
                raise TargetError(f"verify failed for segment at {addr:#x} via {mem.name}")
        return int(elf.header["e_entry"])


def elf_symbol(path: Path, name: str) -> int:
    """Address of a symbol in an ELF."""
    with open(path, "rb") as f:
        for section in ELFFile(f).iter_sections():
            if isinstance(section, SymbolTableSection):
                syms = section.get_symbol_by_name(name)
                if syms:
                    return int(syms[0]["st_value"])
    raise ConfigurationError(f"symbol {name!r} not found in {path}")
