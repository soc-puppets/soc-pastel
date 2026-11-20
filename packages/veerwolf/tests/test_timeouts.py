"""Tests that run out of time fail as timed out, on VeeRwolf's model.

Needs a simulation run, e.g.::

    pytest packages/veerwolf/tests/test_timeouts.py --device=veerwolf --platform=verilator

Each case runs a small test suite in its own pytest, as a user would, and
checks that it fails promptly with PASTEL's timeout message.
"""

SPIN = """
#include "pastel/pastel.h"

int test_main(void) {
  for (;;) {
  }
}
"""


def test_firmware_that_never_finishes_times_out(run_suite):
    out, took = run_suite(
        """
        import pytest

        @pytest.mark.pastel_timeout(grace_s=1)
        def test_spin(firmware):
            firmware.run("spin.c", chip_ms=0.5)
        """,
        files={"spin.c": SPIN},
    )
    assert "1 failed" in out, out
    assert "PastelTimeout: timed out: the test took more than 0.5 ms of chip time" in out, out
    assert took < 300, f"took {took:.0f} s"


def test_a_host_side_wait_that_never_ends_times_out(run_suite):
    out, _ = run_suite(
        """
        import time

        import pytest

        @pytest.mark.pastel_timeout(chip_ms=0.5, grace_s=1)
        def test_wait_for_ever(harness):
            harness.ocd.reset(halt=False)
            while True:  # waits on the target, so it fails once the platform stops
                harness.ocd.state()
                time.sleep(0.05)
        """
    )
    assert "1 failed" in out, out
    assert "timed out: the test took more than 0.5 ms of chip time, plus 1 s grace" in out, out


def test_a_test_without_a_limit_passes(run_suite):
    out, _ = run_suite(
        """
        import pytest

        @pytest.mark.pastel_timeout(chip_ms=None)
        def test_no_limit(harness):
            harness.ocd.reset(halt=True)
            assert harness.timeout.settings.chip_ms is None
        """
    )
    assert "1 passed" in out, out
