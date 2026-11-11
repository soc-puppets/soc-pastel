# Contributing to PASTEL

Before sending a change, run what CI runs:

    pip install -e ".[dev]" -e packages/veerwolf
    ruff check . && ruff format --check .
    pytest tests packages/veerwolf/tests

and, for changes to simulation or debug code, the conformance suite:

    pytest --pyargs soc_pastel.conformance --device=veerwolf --platform=verilator

Building the documentation is described in [docs/README.md](docs/README.md).

## PASTEL's HDL

PASTEL's SystemVerilog and DPI C++ (`src/soc_pastel/**/hw`,
`src/soc_pastel/sim/csrc`) end up inside every user's testbench, compiled by
whichever simulator they chose. Simulators accept different subsets of legal
SystemVerilog, and an error inside one of PASTEL's macros is reported at the
user's call site, where it is hard to trace. So PASTEL's own HDL avoids
constructs that some simulators are known to reject, even where the
standard allows them. This is a rule for PASTEL's code only: device packs
and testbenches are free to target a single simulator.

Constructs to avoid:

- **Assignments inside expressions.** `while ((n = f()) >= 13)` is legal,
  but Xcelium rejects it: `*E,EXPRPA: expecting a right parenthesis`.
  Assign first, then test:

      n = f();
      while (n >= 13) begin
        ...
        n = f();
      end

`tests/test_hdl_portability.py` checks the HDL in this repository for these.
Add to this list, and to the check where it can be automated, when a
simulator turns out to reject something PASTEL uses.
