"""What PASTEL shows while tests run, and where it points when they fail.

Simulations are slow, so a run that says nothing until the end isn't much
help. By default PASTEL prints:

* **progress as it happens:** model builds, firmware builds, the simulator
  starting (with its run directory) and connecting, firmware output,
  checkpoints, and errors and warnings, each line tagged with the test;
* **a failure block the moment a test fails:** the error, the test's run
  directory and the logs in it, and the model's build log, so you can start
  looking while the rest of the run continues; and
* **at the end:** each failing test's run directory, and each test's waveform.

``--pastel-quiet`` turns off the progress lines; failure blocks and the end
summary stay. Under pytest-xdist, progress lines from workers can't reach the
terminal, but failure blocks and the summary still appear.

Paths travel in each report's ``user_properties`` (keys starting
``pastel_``), so they reach the terminal from xdist workers too.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

import pytest

#: Report property: the test's run directory.
RUN_DIR = "pastel_run_dir"
#: Report property: the build log of the model the test ran on.
MODEL_LOG = "pastel_model_log"
#: Report property: the test's waveform.
WAVES = "pastel_waves"

#: Logs worth pointing at, in a run directory (relative glob patterns).
RUN_LOGS = ("*.log", "fw/*/build.log")


class LiveLog(logging.Handler):
    """Writes PASTEL's log records to the terminal as they happen, tagged with the test."""

    def __init__(self, terminal: Any, capture: Any = None) -> None:
        super().__init__(logging.INFO)
        self.terminal = terminal
        self.capture = capture  # pytest's capture manager, if capturing
        self.test = ""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            text = record.getMessage()
            if record.exc_info:
                text += "\n" + logging.Formatter().formatException(record.exc_info)
            tag = f"[{self.test}] " if self.test else ""
            colour = (
                {"red": True}
                if record.levelno >= logging.ERROR
                else {"yellow": True}
                if record.levelno >= logging.WARNING
                else {}
            )
            first, *rest = text.splitlines() or [""]
            # During a test pytest captures the process's output; without this
            # the lines would go into the capture (and be dropped if the test
            # passes) rather than to the terminal. pytest's own live logging
            # does the same.
            paused = (
                self.capture.global_and_fixture_disabled()
                if self.capture is not None
                else contextlib.nullcontext()
            )
            with paused:
                self.terminal.ensure_newline()
                self.terminal.write_line(f"pastel {tag}{first}", **colour)
                for line in rest:
                    self.terminal.write_line(f"    {line}", **colour)
        except Exception:  # never let reporting break a test
            self.handleError(record)


def _props(report: pytest.TestReport, key: str) -> list[str]:
    return [v for k, v in report.user_properties if k == key]


class Reporter:
    """The pytest plugin behind PASTEL's live output, failure blocks and summary."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.terminal: Any = None
        self.waves: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str]] = []
        self.live: LiveLog | None = None

    @pytest.hookimpl(trylast=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Attach to the terminal (which exists only once every plugin is configured)."""
        config = self.config
        self.terminal = config.pluginmanager.get_plugin("terminalreporter")
        quiet = config.getoption("pastel_quiet", False)
        worker = hasattr(config, "workerinput")  # an xdist worker has no terminal of its own
        if self.terminal is not None and not quiet and not worker:
            self.live = LiveLog(self.terminal, config.pluginmanager.get_plugin("capturemanager"))
            logger = logging.getLogger("soc_pastel")
            logger.addHandler(self.live)
            if logger.getEffectiveLevel() > logging.INFO:
                logger.setLevel(logging.INFO)

    def unregister(self) -> None:
        """Remove the live handler (at the end of the session)."""
        if self.live is not None:
            logging.getLogger("soc_pastel").removeHandler(self.live)
            self.live = None

    def pytest_runtest_logstart(self, nodeid: str) -> None:  # noqa: D102
        if self.live is not None:
            self.live.test = nodeid.rsplit("::", 1)[-1]

    def pytest_runtest_logfinish(self, nodeid: str) -> None:  # noqa: D102
        if self.live is not None:
            self.live.test = ""

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:  # noqa: D102
        if report.when == "teardown":
            self.waves += [(report.nodeid, w) for w in _props(report, WAVES)]
        if report.failed:
            run_dirs = _props(report, RUN_DIR)
            if run_dirs:
                self.failed.append((report.nodeid, run_dirs[0]))
            self._failure_block(report)

    def _failure_block(self, report: pytest.TestReport) -> None:
        """Print the failure now, with where to look, while the run continues."""
        if self.terminal is None:
            return
        run_dirs = _props(report, RUN_DIR)
        if not run_dirs and not _props(report, MODEL_LOG):
            return  # not a PASTEL test: leave it to pytest
        tr = self.terminal
        if getattr(getattr(tr, "_tw", None), "width_of_current_line", 0):
            tr.write("\n")  # after pytest's own "F"
        what = "failed" if report.when == "call" else f"failed in {report.when}"
        tr.write_sep("_", f"{report.nodeid} {what}", red=True, bold=True)
        text = report.longreprtext.rstrip()
        if text:
            tr.write_line(text)
        for run_dir in run_dirs[:1]:
            tr.write_line(f"run directory: {run_dir}", bold=True)
            root = Path(run_dir)
            logs = sorted({p for pattern in RUN_LOGS for p in root.glob(pattern)})
            for path in logs:
                tr.write_line(f"  {path.relative_to(root)}")
        for model_log in dict.fromkeys(_props(report, MODEL_LOG)):
            tr.write_line(f"model build log: {model_log}")
        tr.write_sep("_", red=True)

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:  # noqa: D102
        if self.failed:
            terminalreporter.write_sep("-", "run directories of failing tests")
            for nodeid, run_dir in dict.fromkeys(self.failed):
                terminalreporter.write_line(f"{nodeid}: {run_dir}")
        if self.waves:
            terminalreporter.write_sep("-", "waveforms")
            for nodeid, path in self.waves:
                terminalreporter.write_line(f"{nodeid}: {path}")

    def pytest_unconfigure(self, config: pytest.Config) -> None:  # noqa: D102
        self.unregister()
