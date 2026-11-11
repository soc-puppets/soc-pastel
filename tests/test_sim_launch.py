"""Simulator launchers, and the listener a remote simulator dials."""

import shlex
import socket
import sys
import threading

import pytest

from soc_pastel.core import ConfigurationError
from soc_pastel.core.endpoints import (
    CONTROL_ENDPOINT,
    EndpointInfo,
    Frame,
    FrameKind,
    Hello,
    encode_frame,
)
from soc_pastel.sim.handlers import PlatformControl
from soc_pastel.sim.launch import (
    LOCAL_CONNECT_TIMEOUT_S,
    REMOTE_CONNECT_TIMEOUT_S,
    CommandLauncher,
    LocalLauncher,
    connect_timeout,
    make_launcher,
)
from soc_pastel.sim.session import Listener, accept_session, plain_address

HANDLERS = {"pastel.platform": PlatformControl}


# -- Launchers -------------------------------------------------------------------------


def test_prefix_template_appends_the_model_command(tmp_path):
    launcher = CommandLauncher("srun -p sim --cpus-per-task=4")
    cmd = launcher.command(["/img/Vsim", "+pastel_host=h:1:t"], tmp_path)
    assert cmd == ["srun", "-p", "sim", "--cpus-per-task=4", "/img/Vsim", "+pastel_host=h:1:t"]
    assert launcher.remote


def test_cmd_template_becomes_one_shell_command(tmp_path):
    run_dir = tmp_path / "with space"
    cmd = CommandLauncher("ssh -o BatchMode=yes simhost {cmd}").command(
        ["/img/Vsim", "+pastel_host=h:1:t"], run_dir
    )
    assert cmd[:4] == ["ssh", "-o", "BatchMode=yes", "simhost"] and len(cmd) == 5
    assert shlex.split(cmd[4]) == [
        "cd",
        str(run_dir),
        "&&",
        "exec",
        "/img/Vsim",
        "+pastel_host=h:1:t",
    ]


def test_cmd_template_runs_in_the_run_directory(tmp_path):
    log = tmp_path / "sim.log"
    run_dir = tmp_path / "run dir"
    run_dir.mkdir()
    with open(log, "wb") as f:
        proc = CommandLauncher("sh -c {cmd}").launch(
            [sys.executable, "-c", "import os; print('cwd=' + os.getcwd())"], run_dir, f
        )
        assert proc.wait(timeout=30) == 0
    text = log.read_text()
    assert "pastel: launching: sh -c" in text
    assert f"cwd={run_dir}" in text


def test_missing_launcher_executable(tmp_path):
    with open(tmp_path / "log", "wb") as f, pytest.raises(ConfigurationError, match="not found"):
        CommandLauncher("no-such-launcher-xyz").launch(["true"], tmp_path, f)


def test_launcher_selection():
    assert type(make_launcher({})) is LocalLauncher
    assert not make_launcher({}).remote
    assert type(make_launcher({"sim_launcher": "srun"})) is CommandLauncher
    plugin = make_launcher({"sim_launcher_class": "soc_pastel.sim.launch:LocalLauncher", "x": 1})
    assert type(plugin) is LocalLauncher and plugin.options["x"] == 1
    with pytest.raises(ConfigurationError, match="not both"):
        make_launcher({"sim_launcher": "srun", "sim_launcher_class": "a:b"})
    with pytest.raises(ConfigurationError, match="not a SimLauncher"):
        make_launcher({"sim_launcher_class": "pathlib:Path"})
    with pytest.raises(ConfigurationError, match="no sim launcher"):
        make_launcher({"sim_launcher_class": "no-such-plugin"})
    with pytest.raises(ConfigurationError, match="empty"):
        make_launcher({"sim_launcher": "  "})


def test_connect_timeout_defaults():
    assert connect_timeout({}, LocalLauncher({})) == LOCAL_CONNECT_TIMEOUT_S
    assert connect_timeout({}, CommandLauncher("srun")) == REMOTE_CONNECT_TIMEOUT_S
    assert connect_timeout({"sim_connect_timeout": 5}, CommandLauncher("srun")) == 5.0


# -- Listening for a remote simulator --------------------------------------------------


def _hello(token):
    eps = (EndpointInfo(0, "platform", "pastel.platform", "tb"),)
    return encode_frame(
        Frame(CONTROL_ENDPOINT, FrameKind.HELLO, Hello(1, token, eps, {"jtag_port": 9}).encode())
    )


def _dial(port, payload, keep):
    s = socket.create_connection(("127.0.0.1", port))
    if payload:
        s.sendall(payload)
    keep.append(s)
    return s


def test_network_listener_spec_and_stray_connections():
    lst = Listener(bind="", advertise="simhost.example")
    assert lst.spec == f"simhost.example:{lst.port}:{lst.token}"
    keep: list[socket.socket] = []

    def clients():
        _dial(lst.port, b"", keep).close()  # a port scan: connect and leave
        _dial(lst.port, b"GET / HTTP/1.0\r\n\r\n", keep)  # garbage
        _dial(lst.port, _hello("wrong"), keep)  # a stale simulator from another run
        _dial(lst.port, _hello(lst.token), keep)  # ours

    t = threading.Thread(target=clients, daemon=True)
    t.start()
    session = accept_session(lst, 20, {}, HANDLERS)
    assert session.hello.info["jtag_port"] == 9
    assert session.peer == "127.0.0.1"  # not ::ffff:127.0.0.1 from the dual-stack socket
    session.close()
    lst.close()
    for s in keep:
        s.close()


def test_ipv6_advertise_is_bracketed():
    lst = Listener(bind="", advertise="fd00::5")
    assert lst.spec.startswith(f"[fd00::5]:{lst.port}:")
    lst.close()


def test_plain_address():
    assert plain_address("::ffff:10.1.2.3") == "10.1.2.3"
    assert plain_address("fd00::5") == "fd00::5"
    assert plain_address("10.1.2.3") == "10.1.2.3"
