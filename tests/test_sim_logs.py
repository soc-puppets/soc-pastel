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
