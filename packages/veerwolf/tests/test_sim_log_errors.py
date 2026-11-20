"""Errors in the simulation's log, on VeeRwolf's model.

Needs a simulation run, e.g.::

    pytest packages/veerwolf/tests/test_sim_log_errors.py --device=veerwolf --platform=verilator

VeeRwolf's testbench prints an error on request: writing b"PASTELER" to the
last RAM word makes it call $error, and b"PASTELTM" makes it print
"TB ERROR: ...", the testbench's own format. Run with -v to see the expected
errors in the live log as XERROR lines.
"""

import pytest

from soc_pastel.sim.logcheck import LogRules

REQUEST = 0x000F_FFF8


@pytest.fixture(autouse=True)
def _simulation_only(request):
    if request.config.getoption("platform") is None:
        pytest.skip("needs a simulation run (--device=veerwolf --platform=...)")


def test_a_simulator_error_is_reported(harness):
    # Without expect_sim_error this test would fail with SimulatorError,
    # quoting the $error line from sim.log.
    harness.expect_sim_error("error requested by the test")
    harness.memory().write(REQUEST, b"PASTELER")


def test_a_testbench_message_is_not_an_error_by_default(harness):
    harness.memory().write(REQUEST, b"PASTELTM")


def test_device_pack_rules_make_testbench_messages_errors(harness, monkeypatch):
    # What a device pack's SimSources(run_log=LogRules(errors=...)) does.
    platform = harness.platform
    monkeypatch.setattr(platform, "RUN_RULES", platform.RUN_RULES.merged(
        LogRules(errors=(r"^TB ERROR:",))
    ))  # fmt: skip
    harness.expect_sim_error("TB ERROR: message requested by the test")
    harness.memory().write(REQUEST, b"PASTELTM")
