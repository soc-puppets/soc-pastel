"""Sidecars: helper processes the simulator runs, reached through pastel.pipe."""

import threading

import pytest

from soc_pastel.core import ConfigurationError, PastelTimeout, Sidecar
from soc_pastel.core.endpoints import EndpointInfo
from soc_pastel.sim.handlers import Pipe, PipeClosed


def make_pipe():
    sent = []
    return Pipe(EndpointInfo(3, "ocd.openocd", "pastel.pipe", "pastel_server"), sent.append), sent


def test_sidecar_rejects_tabs_newlines_and_empty_commands():
    Sidecar("ocd", ("openocd", "-f", "openocd.cfg"), "openocd.log")
    with pytest.raises(ConfigurationError, match="tab or newline"):
        Sidecar("ocd", ("openocd", "-c", "init\nshutdown"), "openocd.log")
    with pytest.raises(ConfigurationError, match="no command"):
        Sidecar("ocd", (), "openocd.log")


def test_pipe_frames_writes_and_reassembles_replies():
    pipe, sent = make_pipe()
    pipe.write(b"version\x1a")
    pipe.close_input()
    assert sent == [b"\x00version\x1a", b"\x01"]
    pipe.on_message(b"\x00Open On-Chip")
    pipe.on_message(b"\x00 Debugger\x1aOK:1")
    assert pipe.read_until(b"\x1a", 1) == b"Open On-Chip Debugger\x1a"
    pipe.on_message(b"\x00\x1a")
    assert pipe.read_until(b"\x1a", 1) == b"OK:1\x1a"


def test_pipe_large_writes_are_chunked():
    pipe, sent = make_pipe()
    pipe.write(b"x" * (Pipe.CHUNK + 10))
    assert [len(m) for m in sent] == [Pipe.CHUNK + 1, 11]


def test_pipe_reports_exit_status_and_signals():
    pipe, _ = make_pipe()
    pipe.on_message(b"\x00partial")
    pipe.on_message(b"\x01" + (127).to_bytes(4, "little", signed=True))
    with pytest.raises(PipeClosed, match="exited with status 127") as e:
        pipe.read_until(b"\x1a", 1)
    assert e.value.exit_status == 127 and pipe.closed

    pipe, _ = make_pipe()
    pipe.on_message(b"\x01" + (-9).to_bytes(4, "little", signed=True))
    with pytest.raises(PipeClosed, match="killed by signal 9"):
        pipe.read_until(b"\x1a", 1)


def test_pipe_wakes_reader_when_simulation_ends():
    pipe, _ = make_pipe()
    threading.Timer(0.1, pipe.on_close).start()
    with pytest.raises(PipeClosed, match="simulation ended") as e:
        pipe.read_until(b"\x1a", 5)
    assert e.value.exit_status is None


def test_pipe_timeout():
    pipe, _ = make_pipe()
    with pytest.raises(PastelTimeout):
        pipe.read_until(b"\x1a", 0.05)
