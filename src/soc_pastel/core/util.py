"""Small helpers shared across PASTEL."""

from __future__ import annotations

import fcntl
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: Log level for errors a test said it expects, like pytest's xfail: a fault
#: with ``expect_fault``, firmware errors with ``expect_pass=False``, simulator
#: errors after ``harness.expect_sim_error()``. Shown wherever INFO is, but not
#: as an error.
XERROR = logging.INFO + 5
logging.addLevelName(XERROR, "XERROR")


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on ``path`` (created if needed) across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def log_tail(path: Path, chars: int = 1500) -> str:
    """The end of the file at ``path``, after a colon and newline, for an error message.

    Empty if the file is missing or empty.
    """
    try:
        tail = path.read_text(errors="replace")[-chars:].strip()
    except OSError:
        return ""
    return f":\n{tail}" if tail else ""


def describe_exit(status: int) -> str:
    """How a process ended, from its exit status (negative: the signal that killed it)."""
    return f"was killed by signal {-status}" if status < 0 else f"exited with status {status}"
