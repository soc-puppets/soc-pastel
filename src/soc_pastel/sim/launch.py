"""Launching a simulator, on this machine or another.

A simulation platform builds a model, then asks a :class:`SimLauncher` to run
it. The simulator dials back to the test process over TCP (see
:mod:`soc_pastel.sim.session`), so the launcher only has to start the process
somewhere that can reach this machine; it never carries the endpoint traffic.

Three ways to choose a launcher, from the command line:

* Nothing: :class:`LocalLauncher` runs the model as a child process.
* ``--sim-launcher=<template>``: :class:`CommandLauncher` runs the model
  through a command such as ``srun -p sim`` or ``ssh simhost {cmd}``.
* ``--sim-launcher-class=<name>``: any :class:`SimLauncher` subclass,
  registered under the ``soc_pastel.launchers`` entry-point group or given as
  ``module:Class``.

A launcher whose :attr:`~SimLauncher.remote` is True makes the platform
listen on every interface, so the simulator may run anywhere on the network.
Helper processes such as OpenOCD run beside the simulator as sidecars (see
:class:`~soc_pastel.core.platform.Sidecar`), so only the simulator's one
outbound connection crosses the network.
"""

from __future__ import annotations

import shlex
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import IO, Any, ClassVar, Protocol

from soc_pastel.core.errors import ConfigurationError

#: How long to wait for a local simulator to connect.
LOCAL_CONNECT_TIMEOUT_S = 120.0
#: How long to wait for a remote one: long enough to sit in a batch queue.
REMOTE_CONNECT_TIMEOUT_S = 3600.0


class SimProcess(Protocol):
    """What a launcher returns: the subset of :class:`subprocess.Popen` used.

    The process stands for the simulation: it must keep running while the
    simulator runs and end when it ends (``srun`` and ``ssh`` do; a bare
    ``sbatch`` or ``qsub``, which return as soon as the job is queued, do not).
    """

    def poll(self) -> int | None:
        """Exit status, or None while running."""

    def wait(self, timeout: float | None = None) -> int:
        """Wait for exit; raise :class:`subprocess.TimeoutExpired` on timeout."""

    def terminate(self) -> None:
        """Ask the simulation to stop."""

    def kill(self) -> None:
        """Stop the simulation now."""


class SimLauncher(ABC):
    """Starts a simulator process.

    Args:
        options: The platform's options (command line and config), so a
            launcher plugin can read options it added itself.
    """

    #: True if the simulator may run on another machine.
    remote: ClassVar[bool] = True

    def __init__(self, options: Mapping[str, Any]) -> None:
        self.options = options

    @abstractmethod
    def launch(self, argv: Sequence[str], cwd: Path, log: IO[bytes]) -> SimProcess:
        """Start ``argv`` with working directory ``cwd``, output going to ``log``.

        ``argv[0]`` and ``cwd`` are absolute paths on this machine; a remote
        launcher relies on the same paths existing where the simulator runs
        (a shared file system). The simulator writes its waveform into ``cwd``.
        """


class LocalLauncher(SimLauncher):
    """Run the simulator as a child process of the test."""

    remote: ClassVar[bool] = False

    def launch(self, argv: Sequence[str], cwd: Path, log: IO[bytes]) -> SimProcess:  # noqa: D102
        return subprocess.Popen(list(argv), cwd=cwd, stdout=log, stderr=subprocess.STDOUT)


class CommandLauncher(SimLauncher):
    """Run the simulator through a command template.

    Without a ``{cmd}`` placeholder the template is a prefix: the model's
    command line is appended and the command runs in the run directory. Use
    this for launchers that keep the working directory, such as::

        srun --partition=sim --cpus-per-task=4
        qrsh -cwd -now no
        bsub -K -I

    With ``{cmd}``, that token is replaced by a single shell command that
    changes to the run directory and runs the model. Use this for launchers
    that take a shell command, such as::

        ssh simhost {cmd}
        docker exec simbox sh -c {cmd}
    """

    PLACEHOLDER: ClassVar[str] = "{cmd}"

    def __init__(self, template: str, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(options or {})
        self.template = shlex.split(template)
        if not self.template:
            raise ConfigurationError("--sim-launcher is empty")

    def command(self, argv: Sequence[str], cwd: Path) -> list[str]:
        """The full command line that runs ``argv`` in ``cwd``."""
        if not any(self.PLACEHOLDER in t for t in self.template):
            return [*self.template, *argv]
        shell = f"cd {shlex.quote(str(cwd))} && exec {shlex.join(argv)}"
        return [t.replace(self.PLACEHOLDER, shell) for t in self.template]

    def launch(self, argv: Sequence[str], cwd: Path, log: IO[bytes]) -> SimProcess:  # noqa: D102
        cmd = self.command(argv, cwd)
        log.write(f"pastel: launching: {shlex.join(cmd)}\n".encode())
        log.flush()
        try:
            return subprocess.Popen(
                cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
            )
        except FileNotFoundError:
            raise ConfigurationError(f"sim launcher {cmd[0]!r} not found") from None


def _load_class(ref: str) -> type[SimLauncher]:
    if ":" in ref:
        module, _, attr = ref.partition(":")
        try:
            cls = getattr(import_module(module), attr)
        except (ImportError, AttributeError) as e:
            raise ConfigurationError(f"cannot load sim launcher {ref!r}: {e}") from None
    else:
        from soc_pastel.core import registry

        cls = registry.launcher(ref)
    if not (isinstance(cls, type) and issubclass(cls, SimLauncher)):
        raise ConfigurationError(f"sim launcher {ref!r} is not a SimLauncher subclass")
    return cls


def make_launcher(options: Mapping[str, Any]) -> SimLauncher:
    """The launcher selected by ``--sim-launcher-class`` or ``--sim-launcher``."""
    ref = options.get("sim_launcher_class")
    template = options.get("sim_launcher")
    if ref and template:
        raise ConfigurationError("give --sim-launcher or --sim-launcher-class, not both")
    if ref:
        return _load_class(ref)(options)
    if template:
        return CommandLauncher(template, options)
    return LocalLauncher(options)


def connect_timeout(options: Mapping[str, Any], launcher: SimLauncher) -> float:
    """How long to wait for the simulator to dial back."""
    value = options.get("sim_connect_timeout")
    if value is not None:
        return float(value)
    return REMOTE_CONNECT_TIMEOUT_S if launcher.remote else LOCAL_CONNECT_TIMEOUT_S
