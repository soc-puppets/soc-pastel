# Getting started

## Prerequisites

PASTEL itself is pure Python. Running tests in simulation needs:

| Tool | Notes |
|---|---|
| Python 3.10+ | |
| Verilator 5 | Tested with 5.020 (Ubuntu 24.04 package) |
| RISC-V GCC | `riscv64-unknown-elf-` prefix by default; set `PASTEL_RISCV_PREFIX` otherwise |
| OpenOCD 0.12+ | With the `remote_bitbang` driver (stock builds have it); set `PASTEL_OPENOCD` to choose a binary |
| Git, FuseSoC | FuseSoC is installed with PASTEL |

The VeeRwolf device pack also needs Perl's `Bit::Vector` module, which the
VeeR EL2 configuration generator uses (`libbit-vector-perl` on Debian/Ubuntu).

On Ubuntu 24.04:

    sudo apt-get install verilator gcc-riscv64-unknown-elf openocd libbit-vector-perl

## Install and run the conformance suite

    pip install soc-pastel soc-pastel-veerwolf
    pytest --pyargs soc_pastel.conformance --device=veerwolf --platform=verilator

The first run fetches VeeRwolf (pinned to a tested commit) and its
dependencies, and builds the Verilator model: allow a few minutes. Later runs
reuse both. Everything is cached under `.pastel/` in the current directory;
add it to `.gitignore`.

Useful options:

| Option | Meaning |
|---|---|
| `--device` | Device pack to use (`pastel list` shows installed ones) |
| `--platform` | Platform to run on (`verilator`) |
| `--variant` | Device build; normally chosen from the platform |
| `--code-location` | Where firmware runs, e.g. `ram` or `ccm` for VeeRwolf |
| `-n N` | Run tests in parallel (with `pytest-xdist`) |

Each test's simulator and OpenOCD logs are in its pytest `tmp_path`
(`/tmp/pytest-of-$USER/...`).

## Writing a test

A host-side test receives a harness:

```python
def test_scratch_register(harness):
    harness.ocd.reset(halt=True)
    harness.ocd.memory.write32(0x1000, 0x1234)
    assert harness.memory().read32(0x1000) == 0x1234  # backdoor in simulation
```

A firmware test pairs a C file with a few lines of Python:

```c
// fw/blink.c
#include "pastel/pastel.h"

int test_main(void) {
  pastel_printf("running from 0x%08x\n", (unsigned)(uintptr_t)&test_main);
  return 0;   // non-zero fails the test
}
```

```python
def test_blink(firmware):
    result = firmware.run("fw/blink.c")
    assert result.passed, result.output
```

Declare what a test needs and it is skipped where that isn't available:

```python
@pytest.mark.requires("backdoor")
def test_fast_fill(harness): ...
```

## Inspecting a device

    pastel list
    pastel describe veerwolf
