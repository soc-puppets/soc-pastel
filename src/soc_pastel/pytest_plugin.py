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

import logging
from collections.abc import Iterable, Iterator, Mapping
from importlib import import_module
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

from .core import registry
from .core.device import Device
from .core.errors import ConfigurationError, PastelTimeout, SimulatorError
from .core.harness import PlatformHarness
from .core.interface import Interface
from .core.timeout import TimeoutSettings
from .core.util import XERROR
from .reporting import MODEL_LOG, RUN_DIR, WAVES, Reporter, turn_on_live_logging
from .sim.logcheck import LogFindings, split_expected

try:  # pytest internals; see pytest_runtest_setup below
    from _pytest.tmpdir import tmppath_result_key as _TMP_PATH_RESULTS
except ImportError:  # pragma: no cover - a pytest without the problem
    _TMP_PATH_RESULTS = None


#: Suite-wide timeout settings: ini name, default, help.
_TIMEOUT_INI = [
    (
        "pastel_timeout_chip_ms",
        "100",
        "Each test's time limit, in ms of chip time ('none' for no limit).",
    ),
    (
        "pastel_timeout_grace_s",
        "10",
        "Wall-clock seconds allowed after a test's chip time runs out.",
    ),
    (
        "pastel_timeout_stall_s",
        "600",
        "Wall-clock seconds simulation time may stand still ('none' never to check).",
    ),
]


def _number(text: str, name: str) -> float | None:
    if str(text).strip().lower() in ("", "none", "off"):
        return None
    try:
        return float(text)
    except ValueError:
        raise pytest.UsageError(f"{name}: expected a number or 'none', not {text!r}") from None


def timeout_settings(item: pytest.Item) -> TimeoutSettings:
    """A test's time limits: the suite's ini settings, then its ``pastel_timeout`` marker."""
    config = item.config
    if config.getoption("pastel_no_timeout"):
        return TimeoutSettings()
    values = {
        key: _number(config.getini(name), name)
        for key, (name, _, _) in zip(("chip_ms", "grace_s", "stall_s"), _TIMEOUT_INI, strict=True)
    }
    marker = item.get_closest_marker("pastel_timeout")
    if marker is not None:
        unknown = set(marker.kwargs) - set(values)
        if unknown or marker.args:
            raise pytest.UsageError(
                f"{item.nodeid}: pastel_timeout takes chip_ms=, grace_s= and stall_s="
            )
        values.update(marker.kwargs)
    if values["grace_s"] is None:
        values["grace_s"] = 0.0
    return TimeoutSettings(**values)


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
    group.addoption(
        "--pastel-quiet",
        action="store_true",
        help="Don't turn on pytest's live logging (PASTEL's progress lines) by default.",
    )
    group.addoption(
        "--waves",
        action="store_true",
        help="Dump waveforms from the start of each test, on platforms that can.",
    )
    group.addoption(
        "--pastel-no-timeout",
        action="store_true",
        help="Don't time tests out (for debugging interactively).",
    )
    for name, default, text in _TIMEOUT_INI:
        parser.addini(name, text, default=default)
    sim = parser.getgroup("soc-pastel simulation")
    sim.addoption(
        "--sim-launcher",
        metavar="TEMPLATE",
        help="Run the simulator through this command, e.g. 'srun -p sim' (the model's "
        "command line is appended) or 'ssh simhost {cmd}' ({cmd} becomes a shell command).",
    )
    sim.addoption(
        "--sim-launcher-class",
        metavar="NAME",
        help="Run the simulator with a SimLauncher plugin: a 'soc_pastel.launchers' "
        "entry-point name or 'module:Class'.",
    )
    sim.addoption(
        "--sim-host-address",
        metavar="ADDR",
        help="Address a remote simulator dials to reach this machine "
        "(default: this machine's fully qualified host name).",
    )
    sim.addoption(
        "--sim-connect-timeout",
        type=float,
        metavar="SECONDS",
        help="How long to wait for the simulator to connect "
        "(default 120, or 3600 with a launcher, to allow for queueing).",
    )
    probe = parser.getgroup("soc-pastel debug probes")
    probe.addoption(
        "--jlink-ip",
        metavar="ADDR",
        help="J-Link to use, over IP or a SEGGER tunnel "
        "('tunnel:<name>[:<password>[:<server>[:<port>]]]'); jlink driver.",
    )
    probe.addoption(
        "--jlink-serial", metavar="SN", help="USB J-Link to use, by serial number; jlink driver."
    )
    probe.addoption(
        "--jlink-backend",
        choices=("commander", "pylink"),
        help="How the jlink driver talks to the probe: J-Link Commander (JLinkExe) or "
        "SEGGER's library through pylink. Overrides the binding's 'backend'.",
    )
    for name in registry.platform_names():
        try:
            registry.platform(name).add_options(parser)
        except ConfigurationError:
            pass  # reported properly when the platform is selected


log = logging.getLogger("soc_pastel")


def _finish_simulation(item: pytest.Item, *, test_failed: bool) -> None:
    """Stop the test's platform and check how the simulation went.

    Errors in the simulator's log (or a failing exit status) fail a test that
    otherwise passed, unless the test expected them
    (:meth:`~soc_pastel.core.harness.PlatformHarness.expect_sim_error`). If the
    test already failed, they're only logged, so its own failure isn't hidden.
    """
    __tracebackhide__ = True
    harness = getattr(item, "funcargs", {}).get("harness")
    if not isinstance(harness, PlatformHarness):
        return
    if harness.timeout.expired:  # stopped by the watchdog: its log only shows that
        harness.close()
        return
    expected = harness.expected_sim_errors
    if not harness.started:
        if expected and not test_failed:
            raise SimulatorError(
                f"expected a simulator error matching {expected[0]!r}, "
                "but the simulation never started"
            )
        return
    try:
        harness.close()
    except Exception:
        if not test_failed:
            raise
        log.exception("closing the harness failed")
    platform = harness.platform  # closed: doesn't start it again
    image = getattr(platform, "_image", None)
    if image is not None and image.build_log.is_file():
        item.user_properties.append((MODEL_LOG, str(image.build_log)))
    check_run = getattr(platform, "check_run", None)
    if check_run is None:
        return
    findings = check_run()
    if findings.warnings:
        log.warning("simulation log %s", findings.describe(max_lines=5))
    split = split_expected(findings.errors, expected, getattr(platform, "STOP_MESSAGES", ()))
    name = findings.path.name if findings.path else "log"
    for number, line in split.expected:
        where = f"{name}:{number}: " if number else ""
        log.log(XERROR, "%s%s (expected)", where, line.strip())
    problems = []
    if split.unexpected or findings.missing:
        unexpected = LogFindings(split.unexpected, [], findings.missing, findings.path)
        problems.append(f"simulation reported errors:\n{unexpected.describe()}")
    problems += [
        f"expected a simulator error matching {p!r}, but none appeared" for p in split.missing
    ]
    if problems:
        message = "\n".join(problems)
        if test_failed:
            log.error(message)
        else:
            raise SimulatorError(message, findings.path)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item):
    """Check the simulation as part of the test, so its errors fail the test itself."""
    __tracebackhide__ = True
    try:
        result = yield
    except KeyboardInterrupt:
        raise
    except BaseException as e:
        timed_out = _timed_out(item)
        _finish_simulation(item, test_failed=True)
        if timed_out:
            # Whatever failed, it failed because the watchdog stopped the
            # platform: report the timeout, where the test was waiting.
            raise PastelTimeout(timed_out).with_traceback(_test_frames(item, e)) from None
        raise
    timed_out = _timed_out(item)
    _finish_simulation(item, test_failed=timed_out is not None)
    if timed_out:
        raise PastelTimeout(timed_out)
    return result


def _test_frames(item: pytest.Item, error: BaseException) -> TracebackType | None:
    """``error``'s traceback, cut off where it leaves the test's own file."""
    tb = error.__traceback__
    last_in_test = None
    t = tb
    while t is not None:
        if Path(t.tb_frame.f_code.co_filename) == Path(item.path):
            last_in_test = t
        t = t.tb_next
    if last_in_test is not None:
        last_in_test.tb_next = None
    return tb


def _timed_out(item: pytest.Item) -> str | None:
    harness = getattr(item, "funcargs", {}).get("harness")
    return harness.timeout.expired if isinstance(harness, PlatformHarness) else None


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Let Ctrl-C during fixture set-up end the run quietly.

    pytest's ``tmp_path`` teardown reads per-phase results that pytest only
    records once a phase has finished. Interrupted mid-set-up (building a
    model, waiting for the simulator), the record is missing and pytest
    prints a long internal ``KeyError`` traceback after its own short
    ``KeyboardInterrupt`` notice. An empty record avoids that.
    """
    if _TMP_PATH_RESULTS is not None:
        item.stash.setdefault(_TMP_PATH_RESULTS, {})


def pytest_configure(config: pytest.Config) -> None:
    """Register PASTEL's markers and the waveform summary."""
    config.pluginmanager.register(Reporter(), "soc-pastel-reporter")
    turn_on_live_logging(config)
    config.addinivalue_line(
        "markers",
        "requires(*capabilities): skip unless the harness offers every capability "
        "(interface instance names or platform features such as 'backdoor').",
    )
    config.addinivalue_line(
        "markers",
        "pastel_timeout(chip_ms=, grace_s=, stall_s=): this test's time limit, in ms of "
        "chip time, plus wall-clock grace; None for no limit.",
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
    """The harness for one test; skips the test if its requirements are unmet.

    The platform starts when the test first uses the target (see
    :class:`~soc_pastel.core.harness.PlatformHarness`), and stops after the
    test. Simulator and debugger logs are written to the test's ``tmp_path``.
    """
    h = build_harness(request.config, pastel_device, tmp_path)
    h.timeout.configure(timeout_settings(request.node))
    request.node.user_properties.append((RUN_DIR, str(tmp_path)))
    for marker in request.node.iter_markers("requires"):
        missing = sorted(set(marker.args) - h.capabilities)
        if missing:
            pytest.skip(f"requires {', '.join(missing)}")
    try:
        yield h
    finally:
        h.close()
        waves = h.platform.waves_path  # closed: never starts the platform
        if waves is not None and waves.exists():
            request.node.user_properties.append((WAVES, str(waves)))


class Firmware:
    """Builds and runs test firmware for the current harness.

    Source paths are relative to the requesting test file.
    """

    def __init__(self, harness: PlatformHarness, test_dir: Path, build_dir: Path, location: str):
        self.harness = harness
        self.test_dir = test_dir
        self.build_dir = build_dir
        self.location = harness.variant.code_location(location)
        #: The ELF most recently built, by :meth:`build` or :meth:`run`.
        self.elf: Path | None = None
        from .fw.runner import HostLink

        self._link = HostLink()

    def send(self, data: bytes) -> None:
        """Send up to 255 bytes to the running firmware (``pastel_receive()``).

        Call it from ``on_checkpoint``, while the firmware runs.
        """
        self._link.send(data)

    def send_u32(self, value: int) -> None:
        """Send a 32-bit value to the running firmware (``pastel_receive_u32()``)."""
        self._link.send_u32(value)

    def symbol(self, name: str) -> int:
        """The address of ``name`` in the firmware most recently built.

        Use it in ``on_checkpoint`` to read or write a firmware variable while
        the firmware waits: :meth:`run` builds before it starts the target.
        """
        from .core.errors import ConfigurationError
        from .core.loader import elf_symbol

        if self.elf is None:
            raise ConfigurationError("no firmware built yet: call build() or run() first")
        return elf_symbol(self.elf, name)

    def build(
        self,
        *sources: str | Path,
        include_dirs: Iterable[str | Path] = (),
        defines: Iterable[str] = (),
        **kwargs: Any,
    ) -> Path:
        """Compile sources with the PASTEL runtime; return the ELF path.

        ``include_dirs`` are relative to the test file, like the sources, and
        searched after the variant's own (see its ``fw_build`` hook).
        """
        from .fw.build import build_firmware

        h = self.harness
        paths = [self.test_dir / s for s in sources]
        dirs = [self.test_dir / d for d in include_dirs]
        self.elf = build_firmware(
            h.device,
            h.variant,
            self.location,
            paths,
            self.build_dir,
            include_dirs=dirs,
            defines=defines,
            **kwargs,
        )
        return self.elf

    def run(
        self,
        *sources: str | Path,
        include_dirs: Iterable[str | Path] = (),
        defines: Iterable[str] = (),
        stack_size: int | None = None,
        chip_ms: float | None = None,
        timeout_s: float | None = None,
        expect_pass: bool = True,
        expect_fault: bool = False,
        **kwargs: Any,
    ):
        """Build and run firmware; return its :class:`~soc_pastel.fw.runner.FirmwareResult`.

        ``include_dirs``, ``defines`` and ``stack_size`` go to :meth:`build`; the
        rest (``on_checkpoint``, ...) to :func:`~soc_pastel.fw.runner.run_firmware`.

        ``chip_ms`` gives the firmware its own time limit, in ms of chip time
        from when it starts, in place of the test's (see :ref:`timeouts`);
        ``timeout_s`` adds a plain wall-clock limit.

        Fails the test with :class:`~soc_pastel.fw.runner.FirmwareFailed` if
        the firmware returns non-zero or reports errors, unless
        ``expect_pass=False``, for tests that check a failure; its errors are
        then logged as expected (XERROR) rather than as errors.

        With ``expect_fault=True`` the firmware is expected to take an
        exception: the fault is logged as expected and returned in
        ``result.fault``, and the test fails if the firmware finishes instead.
        """
        __tracebackhide__ = True
        from .fw.runner import run_firmware

        build_opts: dict[str, Any] = {} if stack_size is None else {"stack_size": stack_size}
        elf = self.build(*sources, include_dirs=include_dirs, defines=defines, **build_opts)
        result = run_firmware(
            self.harness,
            elf,
            chip_ms=chip_ms,
            timeout_s=timeout_s,
            expect_fault=expect_fault,
            expect_errors=not expect_pass,
            link=self._link,
            **kwargs,
        )
        from .fw.runner import FirmwareFailed

        if expect_fault:
            if result.fault is None:
                raise FirmwareFailed(result, "expected a fault, but the firmware finished")
        elif expect_pass and not result.passed:
            raise FirmwareFailed(result)
        return result


@pytest.fixture
def firmware(request: pytest.FixtureRequest, harness: PlatformHarness, tmp_path: Path) -> Firmware:
    """Build and run firmware on the harness, at the selected code location.

    Firmware is built from scratch, into the test's run directory (``fw/``),
    whenever the test builds or runs it: always before the simulator starts.
    """
    location = _option(request.config, "code_location") or harness.variant.code_locations[0].name
    return Firmware(harness, Path(request.path).parent, tmp_path / "fw", location)
