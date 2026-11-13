Running the simulator on another machine
========================================

By default the simulator runs as a child of the test process, on the same
machine. With a *launcher* it can run anywhere that can reach the test machine
over TCP, such as a compute farm node under Slurm or Grid Engine, a bigger
workstation over SSH, or a container. OpenOCD goes with it.

How it works
------------

The test process listens on a port and starts the simulator with
``+pastel_host=<host>:<port>:<token>``. On its first clock edge the
simulator *phones home*: it connects to that address and sends HELLO with the
token. All traffic uses that one connection, including backdoor, platform
control, VIPs and debug. The launcher only starts the process and never
carries traffic.

.. code-block:: text

   test machine                         simulation machine
   ┌──────────────────────┐            ┌─────────────────────────────────┐
   │ pytest               │  one TCP   │ simulator                       │
   │  └ endpoint session ◄├────────────┤  ├ endpoint pump ──┐ stdin/out  │
   │     ├ backdoor, VIPs │ connection │  │                 ▼            │
   │     └ OpenOCD Tcl    │ (outbound  │  │       OpenOCD (sidecar)      │
   │                      │  from sim) │  └ pastel_jtag ◄── Unix socket  │
   └──────────────────────┘            └─────────────────────────────────┘

* With a launcher, the test process listens on every interface and
  advertises this machine's fully qualified host name. Override the address
  with ``--sim-host-address`` when the farm reaches you by a different name or
  IP.
* Connections that don't present the token are logged and dropped, so a
  port scanner or a stale simulator from an earlier run can't take over the
  session.
* The simulator tries every address the host name resolves to (IPv6 and
  IPv4) and retries for about a minute. TCP keepalives keep an idle
  connection alive through firewalls during long stretches of simulation.
* OpenOCD is a *sidecar*: the simulator starts it as a child process on the
  same machine, and it never outlives the simulation. It drives the
  testbench's JTAG server over a Unix socket in a private temporary
  directory. It serves Tcl on its stdin and stdout, which the simulator
  relays over its connection to the test process. Bit-level JTAG traffic
  never crosses the network, and nothing on the simulation machine listens on
  a network port.

Choosing a launcher
-------------------

``--sim-launcher=<template>`` runs the model through a command.

Without ``{cmd}``, the template is a **prefix**: the model's command line is
appended, and the command runs in the test's run directory. Use this form for
launchers that keep the working directory:

.. code-block:: console

   $ pytest ... --sim-launcher="srun --partition=sim --cpus-per-task=4 --mem=8G"
   $ pytest ... --sim-launcher="qrsh -cwd -now no -V"
   $ pytest ... --sim-launcher="bsub -K -q sim"

With ``{cmd}``, that token is replaced by one shell command that changes to
the run directory and runs the model. Use this form for launchers that take a
shell command:

.. code-block:: console

   $ pytest ... --sim-launcher="ssh -o BatchMode=yes simbox {cmd}"
   $ pytest ... --sim-launcher="docker exec -i sim sh -c {cmd}"

The launcher command must keep running for as long as the simulation runs,
and end when it ends. ``srun``, ``qrsh``, ``bsub -K`` and ``ssh`` behave this
way. ``sbatch`` and plain ``qsub`` return as soon as the job is queued, and
PASTEL would report that the simulator exited before connecting. Use
``qsub -sync y``, or a launcher plugin (below).

PASTEL waits up to an hour for a remote simulator to connect, so a job can sit
in the queue. Change the limit with ``--sim-connect-timeout=<seconds>``.

Requirements
------------

**A shared file system.** The model is built on the test machine and run
from the same absolute path on the remote one. The simulator also runs in the
test's run directory, where it writes its waveform. Put both the platform's
build directory and the run directory on a file system the machines share.
For example, with Verilator:

.. code-block:: console

   $ pytest --device=veerwolf --platform=verilator \
       --verilator-workdir=/shared/$USER/pastel \
       --basetemp=/shared/$USER/pytest-runs \
       --sim-launcher="srun -p sim"

pytest's default ``/tmp/pytest-of-$USER`` is usually local to each machine.
In that case the ``{cmd}`` form fails with a ``cd`` error in ``sim.log``, and
``srun`` warns that it couldn't change directory.

**Compatible machines.** A compiled model is usually a native executable
(Verilator's is), so the remote machine needs the same architecture and
compatible system libraries. A commercial simulator must also be installed at
the same path on the remote machine, with access to its licence server.

**OpenOCD on the simulation machine.** OpenOCD runs where the simulator
does, so it must be on that machine's ``PATH``, or at the path given by
``PASTEL_OPENOCD`` or the binding's ``executable``. If it can't be found,
``openocd.log`` says ``cannot run openocd`` and the test fails with
``OpenOCD exited during start-up (is it installed on the simulation
machine?)``.

**Network reachability.** The remote machine must be able to open a TCP
connection to the test machine on an ephemeral port. Nothing needs to connect
the other way.

**The session token.** The token keeps other connections out of the session,
but it isn't a secret from other users on the same machines: it appears in
the simulator's command line.

Output
------

The launcher's output and the simulator's output both go to ``sim.log`` in the
run directory. Its first line is the exact command PASTEL ran. If the
simulator exits or times out before connecting, the error includes the end of
``sim.log``. That usually holds the launcher's own complaint, such as an
unknown host, a refused job or a missing directory.

``openocd.log`` and ``openocd.cfg`` are in the run directory too. OpenOCD
writes its log there from the simulation machine.

Stopping: PASTEL asks the simulator to finish. The simulator keeps running
until OpenOCD has shut down, because OpenOCD may still need JTAG while it does,
then exits. PASTEL then terminates, and finally kills, the launcher process.
A simulator also stops by itself as soon as its connection to the test
process closes, and OpenOCD dies with it. A killed ``ssh`` or ``srun`` never
leaves an orphaned simulation running.

Writing a launcher plugin
-------------------------

For anything a command template can't express, subclass
:class:`~soc_pastel.sim.launch.SimLauncher`, for example to submit with
``sbatch`` and track the job ID. ``launch`` returns an object with the
:class:`subprocess.Popen` methods ``poll``, ``wait``, ``terminate`` and
``kill``:

.. code-block:: python

   import subprocess
   from soc_pastel.sim.launch import SimLauncher

   class MyFarm(SimLauncher):
       remote = True

       def launch(self, argv, cwd, log):
           return subprocess.Popen(
               ["farm-run", "--queue", self.options.get("farm_queue") or "sim", "--", *argv],
               cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
           )

Select it with ``--sim-launcher-class=my_pkg.farm:MyFarm``, or register it
under the ``soc_pastel.launchers`` entry-point group and select it by name:

.. code-block:: toml

   [project.entry-points."soc_pastel.launchers"]
   myfarm = "my_pkg.farm:MyFarm"

The launcher receives every pytest option in ``self.options``, so a plugin
that defines its own options in a ``conftest.py`` can read them there.
