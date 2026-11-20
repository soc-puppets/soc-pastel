"""Shared fixtures for the VeeRwolf pack's tests."""

import subprocess
import sys
import textwrap
import time

import pytest


@pytest.fixture
def run_suite(request, tmp_path):
    """Run a small test suite in its own pytest, with this run's device and platform.

    ``run(source, files={name: text})`` writes ``source`` as ``test_suite.py``
    (and each of ``files`` beside it, e.g. firmware), runs it, and returns its output and
    how long it took. Skips unless this run simulates (``--platform``).
    """
    config = request.config
    if config.getoption("platform") is None:
        pytest.skip("needs a simulation run (--device=veerwolf --platform=...)")

    def run(source: str, files: dict[str, str] | None = None) -> tuple[str, float]:
        for name, text in (files or {}).items():
            (tmp_path / name).write_text(textwrap.dedent(text))
        (tmp_path / "test_suite.py").write_text(textwrap.dedent(source))
        args = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"]
        for option in ("device", "platform", "code_location", "sim_launcher"):
            value = config.getoption(option)
            if value:
                args.append(f"--{option.replace('_', '-')}={value}")
        began = time.monotonic()
        result = subprocess.run(
            [*args, str(tmp_path / "test_suite.py")],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            timeout=900,
        )
        return result.stdout + result.stderr, time.monotonic() - began

    return run
