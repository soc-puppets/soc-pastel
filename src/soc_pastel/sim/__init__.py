"""Simulation infrastructure.

Generic pieces every simulation platform uses; nothing here knows about a
particular chip or interface:

* ``hw/pastel_ep_pkg.sv`` -- the DPI contract between VIPs and the endpoint
  pump (FuseSoC core ``soc-puppets:pastel:ep_pkg``).
* The C endpoint pump and ``pastel_server`` module (Phase 1).
* The Verilator and Xcelium platforms (Phase 1).

Interface-specific VIPs live with their interface packs under
``soc_pastel.interfaces``.
"""
