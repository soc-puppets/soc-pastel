"""How simulation platforms check build and run logs (with a fake simulator)."""

from pathlib import Path
from typing import ClassVar

import pytest

from soc_pastel.core import ConfigurationError
from soc_pastel.sim.logcheck import LogRules
from soc_pastel.sim.simulator import SimulatorImage
from soc_pastel.sim.sources import SimSources


class FakeImage(SimulatorImage):
    """'Builds' by running a shell script that prints `output` and creates the model."""

    tool: ClassVar[str] = "fake"
    BUILD_RULES: ClassVar[LogRules] = LogRules(errors=(r"^FAKE-E",), warnings=(r"^FAKE-W",))
    output = ""
    status = 0

    @property
    def exe(self) -> Path:
        return self.dir / "model"

    def steps(self) -> list[list[str]]:
        script = f"printf '%s' '{self.output}'; touch model; exit {self.status}"
        return [["sh", "-c", script]]


def fake(tmp_path, output="", status=0, build_log=None):
    img = FakeImage(SimSources(top="tb", args=(), build_log=build_log), tmp_path, "sh")
    img.output, img.status = output, status
    return img


def test_clean_build(tmp_path):
    img = fake(tmp_path, "all good\n")
    img.build()
    assert img.is_built() and img.warnings == []
    assert img.build_log.read_text().startswith("$ sh -c")  # the command is echoed


def test_warnings_are_kept_with_the_model(tmp_path):
    img = fake(tmp_path, "FAKE-W one\nok\nFAKE-W two\n")
    img.build()
    assert img.is_built() and img.warnings == ["3: FAKE-W one", "5: FAKE-W two"]


def test_an_error_line_fails_the_build_even_with_exit_status_0(tmp_path):
    img = fake(tmp_path, "FAKE-E broken\n")
    with pytest.raises(ConfigurationError, match="reported errors(.|\n)*FAKE-E broken"):
        img.build()
    assert not img.is_built()  # never left looking built


def test_a_failing_exit_status_fails_the_build(tmp_path):
    img = fake(tmp_path, "something\n", status=3)
    with pytest.raises(ConfigurationError, match="exited with status 3"):
        img.build()
    assert not img.is_built()


def test_device_pack_rules_apply_to_the_build(tmp_path):
    img = fake(tmp_path, "TB-OOPS\nFAKE-W known\n", build_log=LogRules(errors=(r"TB-OOPS",)))
    with pytest.raises(ConfigurationError, match="TB-OOPS"):
        img.build()
    waived = fake(tmp_path / "w", "FAKE-W known\n", build_log=LogRules(ignore=(r"known",)))
    waived.build()
    assert waived.warnings == []


# --- The run check ----------------------------------------------------------------------


@pytest.fixture
def platform(tmp_path, monkeypatch):
    from soc_pastel.core import registry
    from soc_pastel.sim.verilator import VerilatorPlatform

    dev = registry.device("veerwolf")
    p = VerilatorPlatform(dev, dev.variant("sim"), {"run_dir": tmp_path})
    sources = SimSources(top="tb", args=(), run_log=LogRules(errors=(r"TB_FAIL",)))
    monkeypatch.setattr(p, "image", lambda: type("I", (), {"sources": sources})())
    return p


def test_nothing_to_check_if_it_never_ran(platform):
    assert not platform.check_run().failed


def test_run_log_errors_and_device_rules(platform):
    platform._ran = True
    platform.log_path.write_text("hello\n%Error: tb.sv:3: Assertion failed\nTB_FAIL x\n")
    found = platform.check_run()
    assert [n for n, _ in found.errors] == [2, 3]


@pytest.mark.parametrize(
    "status, forced, fails", [(0, False, False), (1, False, True), (1, True, False)]
)
def test_exit_status(platform, status, forced, fails):
    platform._ran = True
    platform.log_path.write_text("ok\n")
    platform.exit_status, platform._forced = status, forced
    found = platform.check_run()
    assert found.failed == fails
    if fails:
        assert found.errors[0] == (0, "simulator exited with status 1")


# --- Expected simulator errors ----------------------------------------------------------

from soc_pastel.core import PlatformHarness, SimulatorError  # noqa: E402
from soc_pastel.sim.logcheck import LogFindings, split_expected  # noqa: E402


def test_split_expected():
    errors = [(0, "simulator exited with status 134"), (3, "%Error: boom"), (5, "%Error: other")]
    split = split_expected(errors, [r"boom", r"never"])
    assert split.expected == [(3, "%Error: boom"), (0, "simulator exited with status 134")]
    assert split.unexpected == [(5, "%Error: other")] and split.missing == ["never"]
    # A failing exit status with no expected error isn't expected.
    alone = split_expected([(0, "simulator exited with status 1")], [r"boom"])
    assert alone.unexpected and not alone.expected and alone.missing == ["boom"]
    assert sorted(split_expected(errors, []).unexpected) == sorted(errors)
    # The simulator reporting that it stopped follows the error, like the exit status.
    stop = [(3, "%Error: boom"), (4, "%Error: tb.sv:88: Verilog $stop")]
    assert split_expected(stop, ["boom"], [r"Verilog \$stop"]).unexpected == []
    assert split_expected(stop[1:], ["boom"], [r"Verilog \$stop"]).unexpected == stop[1:]


class FakeItem:
    def __init__(self, harness):
        self.funcargs = {"harness": harness}
        self.user_properties = []


def fake_harness(errors, expected=(), started=True):
    class Platform:
        _image = None

        def stop(self):
            pass

        def check_run(self):
            return LogFindings(errors=list(errors), path=Path("sim.log"))

    h = PlatformHarness.__new__(PlatformHarness)
    h._platform, h._state, h._started, h._open = Platform(), "open", started, []
    h.expected_sim_errors = list(expected)
    from soc_pastel.core.timeout import Watchdog

    h.timeout = Watchdog(h)
    return h


def finish(harness, test_failed=False):
    from soc_pastel.pytest_plugin import _finish_simulation

    _finish_simulation(FakeItem(harness), test_failed=test_failed)


def test_expected_errors_pass_and_are_logged_as_xerror(caplog):
    caplog.set_level("INFO", logger="soc_pastel")
    finish(fake_harness([(4, "%Error: boom"), (0, "simulator exited with status 134")], ["boom"]))
    lines = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert ("XERROR", "sim.log:4: %Error: boom (expected)") in lines
    assert ("XERROR", "simulator exited with status 134 (expected)") in lines


def test_unexpected_or_missing_errors_fail():
    with pytest.raises(SimulatorError, match="other"):
        finish(fake_harness([(4, "%Error: boom"), (6, "%Error: other")], ["boom"]))
    with pytest.raises(SimulatorError, match="expected a simulator error matching 'boom'"):
        finish(fake_harness([], ["boom"]))
    with pytest.raises(SimulatorError, match="never started"):
        finish(fake_harness([], ["boom"], started=False))


def test_a_test_that_already_failed_keeps_its_own_failure(caplog):
    finish(fake_harness([(4, "%Error: boom")]), test_failed=True)  # doesn't raise
    assert any("boom" in r.getMessage() and r.levelname == "ERROR" for r in caplog.records)
