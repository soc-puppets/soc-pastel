"""What PASTEL shows while tests run, and where it points when they fail.

PASTEL logs each step of a test (model and firmware builds, the simulator
starting and connecting, firmware loading and output, checkpoints, results,
warnings and errors) through Python's :mod:`logging`, and leaves showing them
to pytest:

* **Live logging is on by default.** Unless pytest's live logging is already
  configured (``log_cli``, ``--log-cli-level``, ``log_cli_format``), PASTEL
  turns it on at INFO with a short format, so the steps appear as they
  happen. With ``-v``, each test's lines sit under its name, with its result
  after them. ``--pastel-quiet`` leaves pytest's defaults alone.
* **Failures say where to look.** A failing test's report gets a "PASTEL:
  where to look" section: its run directory, the logs in it, and the model's
  build log. It appears in pytest's failure output (and at once with
  pytest-instafail).
* **The end of the run lists** each failing test's run directory, and each
  test's waveform.

Paths travel in each report's ``user_properties`` (keys starting
``pastel_``), so they reach the terminal from pytest-xdist workers too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

#: Report property: the test's run directory.
RUN_DIR = "pastel_run_dir"
#: Report property: the build log of the model the test ran on.
MODEL_LOG = "pastel_model_log"
#: Report property: the test's waveform.
WAVES = "pastel_waves"
#: Report property: pytest options that repeat the test's simulation exactly.
REPLAY = "pastel_replay"

#: Logs worth pointing at, in a run directory (relative glob patterns).
RUN_LOGS = ("*.log", "sim.cmd", "fw/*/build.log")

#: The live-log format PASTEL sets when the user hasn't chosen one.
LIVE_FORMAT = "%(levelname)-7s %(message)s"
#: The heading of the section added to a failing test's report.
SECTION = "PASTEL: where to look"


def turn_on_live_logging(config: pytest.Config) -> None:
    """Show log records as they happen, unless the user has configured that.

    Call from ``pytest_configure``, before pytest's logging plugin reads the
    options (it configures last).
    """
    if config.getoption("pastel_quiet", False):
        return
    if config.getoption("log_cli_level", None) is None and not config.getini("log_cli"):
        config.option.log_cli_level = "INFO"  # pytest takes a level as "on"
    if config.getoption("log_cli_format", None) is None and config.getini("log_cli_format") is None:
        config.option.log_cli_format = LIVE_FORMAT


def _props(user_properties: Any, key: str) -> list[str]:
    return [v for k, v in user_properties if k == key]


def where_to_look(user_properties: Any) -> str:
    """The text of the report section: run directory, its logs, the model's build log."""
    lines = []
    for run_dir in _props(user_properties, RUN_DIR)[:1]:
        lines.append(f"run directory: {run_dir}")
        root = Path(run_dir)
        logs = sorted({p for pattern in RUN_LOGS for p in root.glob(pattern)})
        lines += [f"  {path.relative_to(root)}" for path in logs]
    lines += [
        f"model build log: {log}" for log in dict.fromkeys(_props(user_properties, MODEL_LOG))
    ]
    lines += [f"repeat with: {opts}" for opts in _props(user_properties, REPLAY)[:1]]
    return "\n".join(lines)


class Reporter:
    """The pytest plugin behind the report sections and the end-of-run summary."""

    def __init__(self) -> None:
        self.waves: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str]] = []

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: Any):
        """Add "where to look" to a failing PASTEL test's report."""
        report = yield
        if report.failed:
            text = where_to_look(item.user_properties)
            if text:
                report.sections.append((SECTION, text))
        return report

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:  # noqa: D102
        if report.when == "teardown":
            self.waves += [(report.nodeid, w) for w in _props(report.user_properties, WAVES)]
        if report.failed:
            run_dirs = _props(report.user_properties, RUN_DIR)
            if run_dirs:
                self.failed.append((report.nodeid, run_dirs[0]))

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:  # noqa: D102
        if self.failed:
            terminalreporter.write_sep("-", "run directories of failing tests")
            for nodeid, run_dir in dict.fromkeys(self.failed):
                terminalreporter.write_line(f"{nodeid}: {run_dir}")
        if self.waves:
            terminalreporter.write_sep("-", "waveforms")
            for nodeid, path in self.waves:
                terminalreporter.write_line(f"{nodeid}: {path}")
