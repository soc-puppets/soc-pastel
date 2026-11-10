"""Waveform dumping: from the host, from firmware, and not at all without ``--waves``."""

import pytest


@pytest.mark.requires("waves")
def test_host_controls_waves(harness):
    assert harness.platform.set_waves(False) is False
    assert harness.platform.set_waves(True) is True


def test_firmware_controls_waves(firmware):
    """Runs everywhere: without waves the firmware's requests are harmless no-ops."""
    result = firmware.run("fw/waves.c")
    assert result.passed
    on = int(firmware.harness.has("waves"))
    assert f"waves off=0 on={on}" in result.output


def test_set_waves_without_capability_is_a_noop(harness):
    if harness.has("waves"):
        pytest.skip("platform is dumping waves")
    assert harness.platform.set_waves(True) is False
