"""Memory access through every available path."""

import pytest

from soc_pastel.core import Access, RegionKind


def _ram_regions(variant):
    return [
        r
        for r in variant.regions
        if r.kind is RegionKind.RAM and Access.RW in r.access and r.debug_access
    ]


def test_debug_memory_round_trip(harness):
    harness.ocd.reset(halt=True)
    for region in _ram_regions(harness.variant):
        addr = region.base + 0x100
        pattern = bytes((i * 37 + 11) & 0xFF for i in range(32))
        harness.ocd.memory.write(addr, pattern)
        assert harness.ocd.memory.read(addr, 32) == pattern, region.name


@pytest.mark.requires("backdoor")
def test_backdoor_and_debug_agree(harness):
    harness.ocd.reset(halt=True)
    backdoor = harness.memory()
    regions = [r for r in harness.variant.regions if r.backdoor]
    assert regions, "a platform with a backdoor should reach at least one region"
    for region in regions:
        addr = region.base + 0x200
        backdoor.write32(addr, 0xA5A5_0000 | region.base >> 16)
        assert harness.ocd.memory.read32(addr) == 0xA5A5_0000 | region.base >> 16
        harness.ocd.memory.write32(addr + 4, 0x5A5A_5A5A)
        assert backdoor.read32(addr + 4) == 0x5A5A_5A5A
