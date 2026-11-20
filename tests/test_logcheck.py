"""Log rules: what counts as an error or a warning in build and run logs."""

import pytest

from soc_pastel.sim.logcheck import COMMON_RUN_RULES, LogRules
from soc_pastel.sim.questa import QuestaPlatform
from soc_pastel.sim.vcs import VcsPlatform
from soc_pastel.sim.verilator import VerilatorImage, VerilatorPlatform
from soc_pastel.sim.xcelium import XceliumPlatform


def test_errors_warnings_required_and_ignore():
    rules = LogRules(
        errors=(r"FAIL",), warnings=(r"WARN",), required=(r"^done$",), ignore=(r"FAIL: 0",)
    )
    found = rules.check_text("start\nWARN one\nFAIL: 0\nFAIL here\ndone\n")
    assert found.errors == [(4, "FAIL here")] and found.warnings == [(2, "WARN one")]
    assert found.missing == [] and found.failed
    assert rules.check_text("start\n").missing == [r"^done$"]
    assert "never printed" in rules.check_text("start\n").describe()


def test_merging_adds_a_device_packs_rules():
    pack = LogRules(errors=(r"TB_ERROR",), ignore=(r"%Warning-UNUSED",))
    rules = VerilatorPlatform.RUN_RULES.merged(pack)
    found = rules.check_text("TB_ERROR: bad\n%Warning-UNUSED: x\n%Warning-WIDTH: y\n")
    assert [n for n, _ in found.errors] == [1] and [n for n, _ in found.warnings] == [3]
    assert VerilatorPlatform.RUN_RULES.merged(None) is VerilatorPlatform.RUN_RULES


@pytest.mark.parametrize(
    "platform, error, warning",
    [
        (VerilatorPlatform, "[1500] %Error: tb.sv:12: Assertion failed in tb", "%Warning: x"),
        (VerilatorPlatform, "%Fatal: tb.sv:20: stop", "%Warning-WIDTH: y"),
        (XceliumPlatform, "xmsim: *E,ASRTST (tb.sv,12): Assertion failed", "xmsim: *W,RNQUIE"),
        (VcsPlatform, 'Error: "tb.sv", 12: tb.chk: at time 15 ns', 'Warning: "tb.sv", 3: w'),
        (VcsPlatform, "Error-[NOA] Null object access", "Warning-[TFIPC] ports"),
        (QuestaPlatform, "# ** Error: tb.sv(12): Assertion failed", "# ** Warning: (vsim-3015)"),
        (QuestaPlatform, "# ** Fatal: (vsim-3421) out of range", "** Warning: x"),
    ],
)
def test_each_simulators_run_messages(platform, error, warning):
    found = platform.RUN_RULES.check_text(f"ok\n{error}\n{warning}\n")
    assert found.errors == [(2, error)] and found.warnings == [(3, warning)]


def test_uvm_summary_lines_with_zero_counts_are_not_errors():
    log = (
        "UVM_INFO tb.sv(10) @ 0: hello\n"
        "--- UVM Report Summary ---\n"
        "UVM_WARNING :    0\nUVM_ERROR :    0\nUVM_FATAL :    0\n"
        "Number of caught UVM_ERROR reports   :    0\n"
    )
    assert not COMMON_RUN_RULES.check_text(log).failed
    found = COMMON_RUN_RULES.check_text(log.replace("UVM_ERROR :    0", "UVM_ERROR :    2"))
    assert found.failed
    assert COMMON_RUN_RULES.check_text("UVM_ERROR tb.sv(5) @ 10: scoreboard mismatch\n").failed


def test_pastels_own_messages_from_inside_the_simulator():
    assert COMMON_RUN_RULES.check_text("pastel: cannot connect to host x (1.2.3.4)\n").failed
    assert COMMON_RUN_RULES.check_text("pastel_jtag: cannot listen on /tmp/x\n").failed
    assert not COMMON_RUN_RULES.check_text("pastel: launching: srun ./Vsim\n").failed
    killed = COMMON_RUN_RULES.check_text(
        "pastel: sidecar ocd still running 5.0 s ...; killing it\n"
    )
    assert not killed.failed and killed.warnings


def test_build_rules_skip_command_echoes_and_catch_compiler_errors(tmp_path):
    from soc_pastel.sim.sources import SimSources

    rules = VerilatorImage(SimSources(top="t", args=()), tmp_path, "verilator", 1).build_rules
    assert not rules.check_text("$ verilator --binary -Wno-fatal %Error-style-arg\n").failed
    assert rules.check_text("obj/Vtb.cpp:3:1: error: expected ';'\n").failed
    assert rules.check_text("%Error: tb.sv:3: syntax error\n").failed
    assert len(rules.check_text("x.cpp:1:2: warning: unused variable\n").warnings) == 1


def test_absent_log_has_no_findings(tmp_path):
    found = COMMON_RUN_RULES.check(tmp_path / "missing.log")
    assert not found.failed and found.path == tmp_path / "missing.log"
