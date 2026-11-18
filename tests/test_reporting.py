"""What PASTEL prints while tests run, and where it points when they fail."""

import subprocess
import sys
import textwrap

import pytest

TESTS = """
import logging

from soc_pastel.reporting import MODEL_LOG, RUN_DIR

log = logging.getLogger("soc_pastel.sim.simulator")


def test_passes():
    log.info("simulator started; run directory /somewhere")


def test_fails(request, tmp_path):
    (tmp_path / "sim.log").write_text("x\\n")
    (tmp_path / "fw" / "hello-ram").mkdir(parents=True)
    (tmp_path / "fw" / "hello-ram" / "build.log").write_text("")
    request.node.user_properties.append((RUN_DIR, str(tmp_path)))
    request.node.user_properties.append((MODEL_LOG, "/models/tb-123/build.log"))
    log.warning("a warning\\nwith two lines")
    assert 1 == 2, "the reason"


def test_not_pastel():
    assert False, "plain pytest failure"
"""


def run(tmp_path, *args):
    (tmp_path / "test_demo.py").write_text(textwrap.dedent(TESTS))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args, str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    return result.stdout + result.stderr


def test_progress_lines_are_tagged_with_the_test(tmp_path):
    out = run(tmp_path)
    assert "pastel [test_passes] simulator started; run directory /somewhere" in out
    assert "pastel [test_fails] a warning\n    with two lines" in out


def test_failure_block_points_at_the_run_directory_and_logs(tmp_path):
    out = run(tmp_path)
    block = out[out.index("test_demo.py::test_fails failed") : out.index("= FAILURES =")]
    assert "the reason" in block
    assert "run directory: " in block
    assert "  sim.log" in block and "  fw/hello-ram/build.log" in block
    assert "model build log: /models/tb-123/build.log" in block
    assert "test_not_pastel failed" not in out  # plain pytest failures are left to pytest
    summary = out[out.index("run directories of failing tests") :]
    assert (
        "test_demo.py::test_fails: " in summary and "test_not_pastel" not in summary.split("\n=")[0]
    )


def test_quiet_keeps_failures_but_drops_progress(tmp_path):
    out = run(tmp_path, "--pastel-quiet")
    assert "pastel [" not in out
    assert "test_demo.py::test_fails failed" in out and "run directory: " in out


def test_failure_blocks_under_xdist(tmp_path):
    pytest.importorskip("xdist")
    out = run(tmp_path, "-n", "2")
    assert "test_demo.py::test_fails failed" in out and "  sim.log" in out
    assert "run directories of failing tests" in out


def run_in_terminal(tmp_path, *args):
    """Like run(), but with pytest's output on a pseudo-terminal, as when run by hand.

    pytest captures a test's output differently on a terminal than on a pipe:
    progress lines were once lost on terminals while tests over pipes passed.
    """
    import os
    import pty

    (tmp_path / "test_demo.py").write_text(textwrap.dedent(TESTS))
    controller, terminal = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args, str(tmp_path)],
        stdin=subprocess.DEVNULL,
        stdout=terminal,
        stderr=terminal,
        cwd=tmp_path,
    )
    os.close(terminal)
    out = b""
    while True:
        try:
            chunk = os.read(controller, 65536)
        except OSError:  # the terminal closed: pytest has exited
            break
        if not chunk:
            break
        out += chunk
    proc.wait()
    os.close(controller)
    text = out.decode(errors="replace").replace("\r", "")
    return __import__("re").sub(r"\x1b\[[0-9;]*m", "", text)  # drop colours


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pseudo-terminal")
def test_progress_and_failures_reach_a_real_terminal(tmp_path):
    out = run_in_terminal(tmp_path)
    assert "pastel [test_passes] simulator started; run directory /somewhere" in out
    assert "test_demo.py::test_fails failed" in out and "  sim.log" in out
