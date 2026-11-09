"""pytest integration, registered through the ``pytest11`` entry point.

Command-line options select what to run on::

    pytest --device=veerwolf --platform=verilator [--variant=sim]

Tests receive a :class:`~soc_pastel.core.PlatformHarness` through the
``harness`` fixture and declare what they need with the ``requires`` marker::

    @pytest.mark.requires("spi0", "backdoor")
    def test_flash_id(harness): ...

A test whose requirements the harness cannot meet is skipped, not failed.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from .core import registry
from .core.device import Device
from .core.errors import ConfigurationError
from .core.harness import PlatformHarness
from .core.interface import Interface


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add PASTEL's options; platforms add their own via ``Platform.add_options``."""
    group = parser.getgroup("soc-pastel")
    group.addoption("--device", help="Device to test (see `pastel list`).")
    group.addoption("--platform", help="Platform to run on (see `pastel list`).")
    group.addoption("--variant", help="Device variant; chosen from the platform if omitted.")
    group.addoption(
        "--code-location",
        help="Where firmware is linked to run; defaults to the variant's first code location.",
    )
    for name in registry.platform_names():
        try:
            registry.platform(name).add_options(parser)
        except ConfigurationError:
            pass  # reported properly when the platform is selected


def pytest_configure(config: pytest.Config) -> None:
    """Register PASTEL's markers."""
    config.addinivalue_line(
        "markers",
        "requires(*capabilities): skip unless the harness offers every capability "
        "(interface instance names or platform features such as 'backdoor').",
    )


def _option(config: pytest.Config, name: str) -> Any:
    return config.getoption(name)


@pytest.fixture(scope="session")
def pastel_device(pytestconfig: pytest.Config) -> Device:
    """The device under test, from ``--device`` (or the only installed device)."""
    name = _option(pytestconfig, "device")
    if name is None:
        names = registry.device_names()
        if len(names) != 1:
            raise pytest.UsageError(
                f"--device is required (installed: {', '.join(names) or 'none'})"
            )
        name = names[0]
    return registry.device(name)


def build_harness(config: pytest.Config, device: Device, run_dir: Path) -> PlatformHarness:
    """Construct (but do not open) the harness selected by the command line."""
    platform_name = _option(config, "platform")
    if platform_name is None:
        raise pytest.UsageError("--platform is required")
    platform_cls = registry.platform(platform_name)
    variant = device.variant_for(
        platform=platform_name, kind=platform_cls.kind.value, requested=_option(config, "variant")
    )
    platform = platform_cls(device, variant, {**vars(config.option), "run_dir": run_dir})

    interfaces: dict[str, tuple[type[Interface], Mapping[str, Any]]] = {}
    for inst_name, binding in device.interfaces.items():
        cfg = binding.resolve(
            variant=variant.name, platform=platform_name, kind=platform_cls.kind.value
        )
        if cfg is not None:  # None: interface absent on this platform
            interfaces[inst_name] = (registry.interface(binding.type, cfg["impl"]), cfg)

    factory = None
    if device.register_model:
        module, _, attr = device.register_model.partition(":")
        factory = getattr(import_module(module), attr)

    harness = PlatformHarness(device, variant, platform, interfaces, register_model=factory)
    return harness


@pytest.fixture
def harness(
    request: pytest.FixtureRequest, pastel_device: Device, tmp_path: Path
) -> Iterator[PlatformHarness]:
    """An open harness for one test; skips the test if its requirements are unmet.

    Simulator and debugger logs are written to the test's ``tmp_path``.
    """
    h = build_harness(request.config, pastel_device, tmp_path)
    for marker in request.node.iter_markers("requires"):
        missing = sorted(set(marker.args) - h.capabilities)
        if missing:
            pytest.skip(f"requires {', '.join(missing)}")
    with h:
        yield h


class Firmware:
    """Builds and runs test firmware for the current harness.

    Source paths are relative to the requesting test file.
    """

    def __init__(self, harness: PlatformHarness, test_dir: Path, build_dir: Path, location: str):
        self.harness = harness
        self.test_dir = test_dir
        self.build_dir = build_dir
        self.location = harness.variant.code_location(location)

    def build(self, *sources: str | Path, **kwargs: Any) -> Path:
        """Compile sources with the PASTEL runtime; return the ELF path."""
        from .fw.build import build_firmware

        h = self.harness
        paths = [self.test_dir / s for s in sources]
        return build_firmware(h.device, h.variant, self.location, paths, self.build_dir, **kwargs)

    def run(self, *sources: str | Path, timeout_s: float = 300.0, **kwargs: Any):
        """Build and run firmware; return its :class:`~soc_pastel.fw.runner.FirmwareResult`."""
        from .fw.runner import run_firmware

        return run_firmware(self.harness, self.build(*sources), timeout_s=timeout_s, **kwargs)


@pytest.fixture
def firmware(request: pytest.FixtureRequest, harness: PlatformHarness) -> Firmware:
    """Build and run firmware on the harness, at the selected code location."""
    location = _option(request.config, "code_location") or harness.variant.code_locations[0].name
    build_dir = Path(request.config.rootpath) / ".pastel" / "firmware" / harness.device.name
    return Firmware(harness, Path(request.path).parent, build_dir, location)
