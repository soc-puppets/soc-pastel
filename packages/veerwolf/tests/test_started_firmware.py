"""Firmware started with firmware.start(), as a user's suite sees it, on VeeRwolf's model.

Needs a simulation run, e.g.::

    pytest packages/veerwolf/tests/test_started_firmware.py --device=veerwolf --platform=verilator
"""

FAULT = """
#include "pastel/pastel.h"

int test_main(void) {
  pastel_checkpoint(1, 0);
  __asm__ volatile(".word 0");  // an illegal instruction
  return 0;
}
"""

HELD = """
#include "pastel/pastel.h"

int test_main(void) {
  pastel_checkpoint(1, 1);  // held until the test releases it
  pastel_checkpoint(2, 0);
  return 0;
}
"""


def test_a_fault_in_a_run_the_test_left_going_fails_the_test(run_suite):
    out, _ = run_suite(
        """
        import time

        def test_leaves_it_running(firmware):
            run = firmware.start("fault.c")
            run.wait_checkpoint(1)
            time.sleep(1)  # the firmware faults meanwhile; the test never waits
        """,
        files={"fault.c": FAULT},
    )
    assert "1 failed" in out, out
    assert "FirmwareFault: firmware fault: mcause=0x2" in out, out


def test_waiting_while_the_firmware_is_held_is_explained(run_suite):
    out, _ = run_suite(
        """
        import pytest

        @pytest.mark.pastel_timeout(chip_ms=1, grace_s=4)
        def test_forgets_to_release(firmware):
            run = firmware.start("held.c")
            run.wait_checkpoint(2)  # never comes: checkpoint 1 is held
        """,
        files={"held.c": HELD},
    )
    assert "1 failed" in out, out
    assert "the firmware is held at waiting checkpoint 1" in out, out
    assert "PastelTimeout: timed out" in out, out
