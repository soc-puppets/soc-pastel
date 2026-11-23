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

- **Relying on 2-state start-up values.** Verilator is a 2-state simulator,
  so a signal nothing has driven or reset reads as 0 there, but as X in a
  4-state simulator. Give everything PASTEL drives into the design a defined
  value from time 0. For example, `pastel_jtag` holds TRST asserted until
  OpenOCD releases it, as lowRISC's jtagdpi does. A TAP that only TRST resets
  (VeeR's, for one) then starts in reset rather than X.

`tests/test_hdl_portability.py` checks the HDL in this repository for the
first of these.
Add to this list, and to the check where it can be automated, when a
simulator turns out to reject something PASTEL uses.

## Releasing

Releases are published to [PyPI](https://pypi.org/project/soc-pastel/) by
`.github/workflows/pypi-release.yml`, using trusted publishing (no API tokens).
The version comes from the git tag (hatch-vcs): tag `v0.2.0` builds 0.2.0;
between tags, builds are dev versions such as `0.2.1.dev3`. Nothing in the
repository holds a version number to bump.

To release:

1. Check `main` is green in CI and has what you want to release. In
   `docs/getting-started.rst`, update the tag in the command that installs the
   VeeRwolf pack from the repository (`...soc-pastel.git@vX.Y.Z#subdirectory=...`)
   to the version you're about to release.
2. On GitHub, **Releases → Draft a new release**: a new tag `vX.Y.Z` on
   `main`, a title, and **Generate release notes**. Tick **Set as a
   pre-release** for a version like `v0.2.0rc1`. **Publish release**.
3. The workflow builds the package and checks it: the version must match the
   tag, and the wheel must install and work in a clean environment.
4. It then waits for approval of the `pypi-release` environment. Approve the
   run (**Review deployments**), and it publishes to PyPI and attaches the
   files to the GitHub release.

For a dry run, run the workflow by hand (**Actions → Release → Run
workflow**): it builds and checks the chosen branch and publishes nothing.

A version can't be uploaded twice. If a release goes wrong after reaching
PyPI, fix it and release the next version; don't reuse the tag.

One-time set-up (done by a maintainer with admin rights):

- **PyPI** (soc-pastel → Manage → Publishing): add a trusted publisher for
  GitHub: owner `soc-puppets`, repository `soc-pastel`, workflow
  `pypi-release.yml`, environment `pypi-release`.
- **GitHub** (Settings → Environments): create `pypi-release`, with the
  maintainers as required reviewers.

The VeeRwolf pack (`packages/veerwolf`) isn't published: it's the reference
pack, installed from the repository.
