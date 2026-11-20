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


def test_progress_lines_are_shown_live_by_default(tmp_path):
    out = run(tmp_path, "-v")
    block = out[out.index("test_demo.py::test_passes") :]
    assert "live log call" in block
    assert "INFO    simulator started; run directory /somewhere" in block
    assert "WARNING a warning" in out


def test_the_users_own_live_logging_settings_win(tmp_path):
    out = run(tmp_path, "-v", "--log-cli-level=WARNING")
    assert "simulator started" not in out.split("= FAILURES =")[0]  # INFO not shown live
    assert "WARNING a warning" in out
    out = run(tmp_path, "-v", "-o", "log_cli_format=<<%(message)s>>")
    assert "<<simulator started; run directory /somewhere>>" in out


def test_failures_say_where_to_look(tmp_path):
    out = run(tmp_path)
    failures = out[out.index("= FAILURES =") :]
    fails = failures[failures.index("_ test_fails _") : failures.index("_ test_not_pastel _")]
    assert "the reason" in fails
    section = fails[fails.index("PASTEL: where to look") :]
    assert "run directory: " in section
    assert "  sim.log" in section and "  fw/hello-ram/build.log" in section
    assert "model build log: /models/tb-123/build.log" in section
    plain = failures[failures.index("_ test_not_pastel _") :]
    assert "PASTEL: where to look" not in plain.split("\n=")[0]  # plain pytest tests left alone
    summary = out[out.index("run directories of failing tests") :]
    assert "test_demo.py::test_fails: " in summary
    assert "test_not_pastel" not in summary.split("\n=")[0]


def test_quiet_leaves_pytests_defaults_alone(tmp_path):
    out = run(tmp_path, "-v", "--pastel-quiet")
    assert "live log call" not in out
    assert "PASTEL: where to look" in out  # failures still say where to look


def test_failures_say_where_to_look_under_xdist(tmp_path):
    pytest.importorskip("xdist")
    out = run(tmp_path, "-n", "2")
    assert "PASTEL: where to look" in out and "  sim.log" in out
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
    out = run_in_terminal(tmp_path, "-v")
    assert "INFO    simulator started; run directory /somewhere" in out
    assert "PASTEL: where to look" in out and "  sim.log" in out
