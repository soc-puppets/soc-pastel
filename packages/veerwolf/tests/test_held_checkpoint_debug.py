"""A started run left held at a waiting checkpoint: the warning, then a timeout.

A debug vehicle, not run in CI: it can only end by timing out, so it needs a
tight limit, which makes it timing-sensitive on a slow machine. Run it by hand::

    pytest packages/veerwolf/tests/test_held_checkpoint_debug.py \\
        --device=veerwolf --platform=verilator
"""

HELD = """
#include "pastel/pastel.h"

int test_main(void) {
  pastel_checkpoint(1, 1);  // held until the test releases it
  pastel_checkpoint(2, 0);
  return 0;
}
"""


def test_waiting_while_the_firmware_is_held_is_explained(run_suite):
    out, _ = run_suite(
        """
        import pytest

        # The test's default budget covers loading; start(chip_ms=1) then gives
        # the running firmware its own, counted from when it starts.
        @pytest.mark.pastel_timeout(grace_s=8)
        def test_forgets_to_release(firmware):
            run = firmware.start("held.c", chip_ms=1)
            run.wait_checkpoint(2)  # never comes: checkpoint 1 is held
        """,
        files={"held.c": HELD},
    )
    assert "1 failed" in out, out
    assert "the firmware is held at waiting checkpoint 1" in out, out
    assert "PastelTimeout: timed out" in out, out
