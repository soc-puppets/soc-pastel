# soc-pastel-veerwolf

PASTEL device pack for [VeeRwolf](https://github.com/chipsalliance/VeeRwolf),
the FuseSoC-based reference SoC for the VeeR RISC-V cores, built with VeeR EL2.

It is the reference device PASTEL's conformance suite runs against, and the
worked example for writing a device pack.

VeeRwolf itself is pulled in as a FuseSoC dependency, pinned to a specific
upstream commit; it is not redistributed here. Its RTL is licensed under
Apache-2.0 (per-file SPDX headers).

Host prerequisites beyond PASTEL's: Verilator, FuseSoC, a RISC-V GCC
toolchain, and Perl's `Bit::Vector` module (`libbit-vector-perl` on
Debian/Ubuntu), which the VeeR EL2 configuration generator needs.

Run the PASTEL conformance suite against it:

    pytest --pyargs soc_pastel.conformance --device=veerwolf --platform=verilator

The pack fetches VeeRwolf into `.pastel/verilator/sources/fusesoc` on first
use. Set `PASTEL_VEERWOLF_WORKSPACE` to use an existing FuseSoC workspace
that already has the `veerwolf` and `fusesoc-cores` libraries.
