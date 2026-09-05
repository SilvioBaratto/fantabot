"""Long-running fantabot commands, supervised as child processes.

**A subprocess, not a thread**, and the reason is in the things it runs rather than in
taste. `adapters/files/landing.py` states the invariant: a frame that never reached disk
is gone, and an evening of auctions does not come back. `jobs.py` accepts that a daemon
thread dies with the server on the grounds that every fantabot write is an upsert — true
of the loader, **false of the collector**. `application/harvest_loader.py` also records a
pass holding ~17x its 32 MB window (1.6 GB resident measured), which inside the SPA
process means the UI stalls on a timer. A child owns its own memory and outlives its
parent; both problems go away with it.

**It is proven against `harvest load --follow` before it is pointed at `collect`.** The
loader is idempotent and restartable, the collector is the thing that cannot be re-run,
and debugging process control against the irreplaceable one is the wrong order.

**The stop sequence** is `SIGINT` to a real pid — *not* `SIGTERM`, which has no handler
on this path: `aste_collect` catches only `KeyboardInterrupt` — then poll at 250 ms for
15 s, then `SIGKILL`. The landing zone is safe under all three: `LandingZone.write`
opens, appends one line and closes per record, chosen because the collector was killed
eleven times in eight hours on 2026-08-26 and lost reconnect time, never a written
record.

**The Windows path is written and unverified**: `CTRL_BREAK_EVENT` into a child created
with `CREATE_NEW_PROCESS_GROUP`. Recorded as untested in `todo/TODO.md` §4, beside the
role lock's own Windows backend, which is unverified for the same reason.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from fantabot_app.api.infrastructure.jobs import BufferingReporter

#: How long a stop waits for the child to go before it stops asking. `SPEC.md` §6.
STOP_GRACE_S = 15.0
#: How often the wait looks. 15 s is the bound, not the cost — a stop that always paid it
#: would make the button feel broken on the one evening anybody uses it.
STOP_POLL_S = 0.25


def fantabot_command(*args: str) -> list[str]:
    """`[sys.executable, "-m", "fantabot", *args]`.

    The `fantabot` console script is on `PATH` only while the CLI's own virtualenv is
    active, and the app runs in a different one. Naming the interpreter this process is
    already running under needs nothing on `PATH` at all — which is what
    `src/fantabot/__main__.py` exists for.
    """
    return [sys.executable, "-m", "fantabot", *args]


class ProcessJob:
    """One supervised child: spawn it, drain its stdout, and stop it on request.

    The reporter is `jobs.BufferingReporter`, so a supervised command's output lands in
    the job log exactly like a thread job's — the UI polls `GET /jobs/{id}` and cannot
    tell which kind it is watching, which is the point.
    """

    def __init__(
        self,
        command: list[str],
        *,
        role: str,
        landing: Path,
        grace_s: float = STOP_GRACE_S,
        poll_s: float = STOP_POLL_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.command = command
        self.role = role
        self.landing = landing
        self.grace_s = grace_s
        self.poll_s = poll_s
        self._clock = clock
        self._sleep = sleep
        self._process: subprocess.Popen[str] | None = None
        #: Set by `run`, so `stop` — which is called from the HTTP handler's thread —
        #: can narrate the stop sequence into the same log the child is writing into.
        self._reporter: BufferingReporter | None = None
        #: Held across `start` and `stop`, which run on different threads: the HTTP
        #: handler calls `stop` while the job thread is still inside `run`.
        self._lock = threading.Lock()

    # -- state ---------------------------------------------------------------------

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def returncode(self) -> int | None:
        process = self._process
        return None if process is None else process.returncode

    @property
    def pid(self) -> int | None:
        process = self._process
        return None if process is None else process.pid

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> subprocess.Popen[str]:
        """Spawn the child, refusing if something already holds the role.

        The lock is taken and released here rather than held: the *child* is the one that
        must hold it for its whole life. That leaves a window between this check and the
        child's own acquisition, and the child closes it — it takes the same lock and
        exits 2 with `RoleBusy`'s message if it loses the race, which lands in this job's
        log. Checking here is what turns "started, then died two seconds later" into a
        refusal the operator sees at the moment they click.

        Killing the app leaves the child running, because it is a subprocess. The next
        start therefore has to find it through the operating system rather than through
        state the dead process was holding — which is exactly what the role lock is.
        """
        from fantabot.adapters.files.lock import role_lock

        with role_lock(self.landing, self.role):
            pass  # RoleBusy propagates; the caller reports it.

        with self._lock:
            # An argv list, never a shell: nothing here is composed from user input.
            self._process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # line-buffered: a job that buffers until exit looks hung
                creationflags=self._creation_flags(),
            )
            return self._process

    def run(self, reporter: BufferingReporter) -> bool:
        """Start the child and stream it into `reporter` until it exits. `True` if ok.

        Read line by line as it arrives, never accumulated and printed at the end: a job
        that buffers until exit is indistinguishable from a hung one, and the evening this
        supervises is three hours long.
        """
        self._reporter = reporter
        process = self.start()
        reporter.print(f"started pid {process.pid}: {' '.join(self.command[2:])}")
        # `stdout` is a pipe because `start` sets one; narrowed rather than asserted,
        # so a future `start` that stopped piping fails here loudly instead of at a
        # stripped `assert`.
        stream = process.stdout
        if stream is not None:
            for line in stream:
                reporter.print(line.rstrip("\n"))
        process.wait()
        reporter.print(f"exited {process.returncode}")
        return process.returncode == 0

    def stop(self) -> None:
        """`SIGINT`, then wait on the role lock, then `SIGKILL`. Safe to call twice.

        `SIGINT` and not `SIGTERM`: `aste_collect` catches only `KeyboardInterrupt`, so a
        `SIGTERM` would take the default action and kill the collector outright — which is
        what the escalation exists to avoid doing first.

        The wait watches the **role lock**, not only the exit status. The lock is what a
        second start consults, so releasing it is the event that matters; and the kernel
        releases it however the holder dies, which a pid check cannot promise.
        """
        process = self._process
        if process is None or process.poll() is not None:
            return

        reporter_pid = process.pid
        self._signal(process)
        deadline = self._clock() + self.grace_s
        while self._clock() < deadline:
            if process.poll() is not None and self._role_is_free():
                return
            self._sleep(self.poll_s)

        # Escalation is announced, never silent: a job that was killed and one that
        # stopped politely are the same row otherwise, and only one of them lost work.
        self._print(f"still running after {self.grace_s:g}s — SIGKILL to pid {reporter_pid}")
        process.kill()
        process.wait()

    # -- the parts that differ by platform -------------------------------------------

    def _creation_flags(self) -> int:
        """Windows needs its own process group to be sendable a `CTRL_BREAK_EVENT`.

        Zero everywhere else, which `Popen` accepts on POSIX — a non-zero value there is
        the only thing it refuses. Read through `getattr` because the constant does not
        exist in the `subprocess` module off Windows at all, so naming it directly is a
        `mypy` error on this machine and an `AttributeError` on the next.

        Unverified on this machine; see the module docstring and `todo/TODO.md` §4.
        """
        if os.name == "nt":  # pragma: no cover - POSIX here
            return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        return 0

    def _signal(self, process: subprocess.Popen[str]) -> None:
        if os.name == "nt":  # pragma: no cover - POSIX here
            self._print(f"stopping: CTRL_BREAK_EVENT to pid {process.pid}")
            process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            return
        self._print(f"stopping: SIGINT to pid {process.pid}")
        process.send_signal(signal.SIGINT)

    # -- helpers ----------------------------------------------------------------------

    def _role_is_free(self) -> bool:
        from fantabot.adapters.files.lock import RoleBusy, role_lock

        try:
            with role_lock(self.landing, self.role):
                return True
        except RoleBusy:
            return False

    def _print(self, message: str) -> None:
        """Say it in the job log, from whichever thread is stopping.

        `stop` runs on the HTTP handler's thread while `run` is blocked reading the
        child's stdout on another. Both append to the same `BufferingReporter.lines`,
        which is a plain list — and `list.append` is atomic under the GIL, so the two
        interleave by line and never by character.
        """
        if self._reporter is not None:
            self._reporter.print(message)
