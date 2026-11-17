"""Checking simulator logs for errors and warnings.

Every simulation platform checks two logs:

* the **build log** of a model, once, when it is built. Any error fails the
  build, even if the tool exited successfully (batch wrappers don't always
  pass the exit status on). Warnings are reported.
* the **run log** of each test, ``sim.log``, when the simulator has exited.
  Any error fails the test. Warnings are reported.

What counts as an error or a warning is a :class:`LogRules`: the platform's
defaults, plus whatever the device pack adds in
:attr:`SimSources.build_log <soc_pastel.sim.sources.SimSources.build_log>`
and :attr:`SimSources.run_log <soc_pastel.sim.sources.SimSources.run_log>`,
e.g. its own error messages, or ``ignore`` patterns for known harmless ones.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LogRules:
    """What to look for in a log. Each entry is a regular expression, searched per line.

    Attributes:
        errors: Lines that mean something went wrong.
        warnings: Lines worth reporting.
        required: Patterns that must each appear somewhere, e.g. a tool's
            "finished" message.
        ignore: Lines skipped before the others are applied: known harmless
            messages, or summary lines that only *mention* errors.
    """

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()

    def merged(self, other: LogRules | None) -> LogRules:
        """These rules plus ``other``'s."""
        if other is None:
            return self
        return LogRules(
            errors=(*self.errors, *other.errors),
            warnings=(*self.warnings, *other.warnings),
            required=(*self.required, *other.required),
            ignore=(*self.ignore, *other.ignore),
        )

    def check_lines(self, lines: Iterable[str], first_line: int = 1) -> LogFindings:
        """Apply the rules to ``lines`` (numbered from ``first_line``)."""
        errors_rx = [re.compile(p) for p in self.errors]
        warnings_rx = [re.compile(p) for p in self.warnings]
        ignore_rx = [re.compile(p) for p in self.ignore]
        seen = dict.fromkeys(self.required, False)
        required_rx = {p: re.compile(p) for p in self.required}
        found = LogFindings()
        for number, raw in enumerate(lines, first_line):
            line = raw.rstrip("\n")
            for p, rx in required_rx.items():
                if not seen[p] and rx.search(line):
                    seen[p] = True
            if any(rx.search(line) for rx in ignore_rx):
                continue
            if any(rx.search(line) for rx in errors_rx):
                found.errors.append((number, line))
            elif any(rx.search(line) for rx in warnings_rx):
                found.warnings.append((number, line))
        found.missing = [p for p, ok in seen.items() if not ok]
        return found

    def check_text(self, text: str) -> LogFindings:
        """Apply the rules to a log's text."""
        return self.check_lines(text.splitlines())

    def check(self, path: Path) -> LogFindings:
        """Apply the rules to the log at ``path`` (an absent log has no findings)."""
        found = LogFindings(path=path)
        if not path.is_file():
            return found
        with open(path, errors="replace") as f:
            result = self.check_lines(f)
        result.path = path
        return result


@dataclass
class LogFindings:
    """What :meth:`LogRules.check` found.

    Attributes:
        errors: ``(line number, line)`` for each error.
        warnings: ``(line number, line)`` for each warning.
        missing: Required patterns that never appeared.
        path: The log, if read from a file.
    """

    errors: list[tuple[int, str]] = field(default_factory=list)
    warnings: list[tuple[int, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    path: Path | None = None

    @property
    def failed(self) -> bool:
        """True if there were errors or a required pattern was missing."""
        return bool(self.errors or self.missing)

    def describe(self, max_lines: int = 10) -> str:
        """Errors, missing patterns and warnings, for a message, with line numbers."""
        out: list[str] = []
        where = f" ({self.path})" if self.path else ""
        for kind, items in (("error", self.errors), ("warning", self.warnings)):
            if items:
                count = f"{len(items)} {kind}{'s' if len(items) != 1 else ''}"
                out.append(f"{count}{where}:")
                out += [f"  {n}: {line.strip()}" for n, line in items[:max_lines]]
                if len(items) > max_lines:
                    out.append(f"  ... and {len(items) - max_lines} more")
        out += [f"never printed: {p!r}" for p in self.missing]
        return "\n".join(out)


#: Run-log rules every simulator shares: UVM and OVL reports, and PASTEL's own
#: messages from inside the simulator.
COMMON_RUN_RULES = LogRules(
    errors=(
        r"UVM_(ERROR|FATAL)\b",
        r"OVL_ERROR",
        r"^pastel(_\w+)?: (cannot|bad|giving up|host error|lost connection)",
    ),
    warnings=(r"UVM_WARNING\b", r"^pastel: sidecar .* killing it"),
    ignore=(
        # UVM's end-of-test summary lists every severity, mostly with count 0.
        r"^(# )?\s*UVM_(ERROR|FATAL|WARNING)\s*:\s*0\s*$",
        r"Number of (demoted|caught) UVM_(FATAL|ERROR|WARNING) reports\s*:\s*0\b",
    ),
)

#: Build-log lines that echo a command (written by PASTEL before each step).
COMMAND_ECHO = r"^\$ "
#: Compiler (gcc, clang) diagnostics, for builds that compile C or C++.
COMPILER_RULES = LogRules(errors=(r": (fatal )?error: ",), warnings=(r": warning: ",))
