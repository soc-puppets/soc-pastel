"""Ctrl-C during a test's set-up ends the run with a short message, not a traceback."""

import subprocess
import sys
import textwrap


def test_interrupt_during_fixture_setup_is_quiet(tmp_path):
    (tmp_path / "test_slow.py").write_text(
        textwrap.dedent(
            """
            import pytest

            @pytest.fixture
            def slow_setup(tmp_path):
                raise KeyboardInterrupt  # as if Ctrl-C arrived while building a model

            def test_it(slow_setup):
                pass
            """
        )
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    out = result.stdout + result.stderr
    assert result.returncode == 2, out  # pytest's "interrupted" exit code
    assert "KeyboardInterrupt" in out
    assert "Traceback" not in out and "KeyError" not in out, out
