# soc-pastel

**PASTEL**: Platform Agnostic Silicon Test Environment & Library.

Write chip-level tests once, in pytest and C, and run them on simulation
(Verilator, Xcelium, ...), FPGA prototypes and silicon. PASTEL builds and loads
the test firmware, drives the chip through its debug port and pins, and checks
the result the same way on every platform.

```shell
pip install soc-pastel
```

Then install a device pack for your chip, and run PASTEL's conformance suite
against it:

```shell
pytest --pyargs soc_pastel.conformance --device=<your device> --platform=verilator
```

[![Documentation](https://readthedocs.org/projects/soc-pastel/badge/?version=latest)](https://soc-pastel.readthedocs.io)

See the [documentation](https://soc-pastel.readthedocs.io) to get started,
write tests, or write a device pack for your chip, and
[CONTRIBUTING.md](https://github.com/soc-puppets/soc-pastel/blob/main/CONTRIBUTING.md)
to work on PASTEL itself.
