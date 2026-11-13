"""Simulation infrastructure.

Generic pieces every simulation platform uses; nothing here knows about a
particular chip or interface:

* ``hw/pastel_ep_pkg.sv`` -- the DPI contract between VIPs and the endpoint
  pump (FuseSoC core ``soc-puppets:pastel:ep_pkg``).
* The C++ endpoint pump and the ``pastel_server`` module, written for any
  simulator with SystemVerilog DPI.
* Launching a simulator, locally or on another machine (:mod:`.launch`), and
  the host side of its connection (:mod:`.session`, :mod:`.handlers`).
* The simulation platforms. Verilator is the only one so far; others reuse
  everything above and add only how to build and run a model.

Interface-specific VIPs live with their interface packs under
``soc_pastel.interfaces``.
"""
