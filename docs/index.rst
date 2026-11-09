PASTEL
======

*Platform Agnostic Silicon Test Environment & Library.*

Write chip-level tests once, during design, and run them unchanged in
simulation, on FPGA prototypes and on silicon.

PASTEL is a pytest plugin plus the pieces each platform needs: a simulation
kit (an endpoint protocol, a JTAG server, backdoor memory access), on-chip
debug drivers, a firmware runtime with RTT and a host mailbox, and a
conformance suite every device must pass. Everything specific to a chip lives
in a *device pack*; the core knows nothing about any particular chip.

.. toctree::
   :maxdepth: 2
   :caption: Using PASTEL

   getting-started
   concepts
   guides/writing-tests
   guides/firmware

.. toctree::
   :maxdepth: 2
   :caption: Supporting a new chip

   guides/device-pack
   guides/troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: Reference

   reference/cli
   reference/protocols
   reference/api
