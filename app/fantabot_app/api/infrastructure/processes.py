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

**The stop sequence** is a flag, then `SIGINT`, then poll at 250 ms for 15 s, then
`SIGKILL`. The landing zone is safe under all four: `LandingZone.write` opens, appends one
line and closes per record, chosen because the collector was killed eleven times in eight
hours on 2026-08-26 and lost reconnect time, never a written record.

**The flag comes first because it is the only part that works everywhere.** The signal
half was `CTRL_BREAK_EVENT` on Windows, and the runner showed what that does: it reaches
a Python child as SIGBREAK and terminates it *before* `except KeyboardInterrupt` runs —
`exited 3221225786`, `STATUS_CONTROL_C_EXIT`, the child's own shutdown line never
printed. Which also made the `SIGKILL` escalation behind it unreachable, so the one
platform that most needed the escalation was the one that could not get there. A polled
file has no such asymmetry: the child chooses when to look. See
`fantabot.adapters.files.stopflag`.

`SIGINT` is still sent on POSIX, and still not `SIGTERM` — `aste_collect` catches only
`KeyboardInterrupt`, so a `SIGTERM` would take the default action and kill the collector
outright, which is what the escalation exists to avoid doing first. It is kept rather
than replaced because it is the proven path here and it ends a child that predates the
flag; the two race, and either one runs the same shutdown.

**Two stages, and the child decides what they mean.** The first stop writes *disarm*, the
second *exit*. `harvest collect` has nothing to disarm and winds down on either, exactly
as its first Ctrl-C already ends it while `asta bid`'s first one only disarms. The flag
carries which stage was asked; the meaning is the command's.

**`CREATE_NEW_PROCESS_GROUP` is kept** even though nothing signals into that group any
more: it is also what stops a console `Ctrl-C` at the app from propagating into a
collector, which is precisely the accident the role lock and the landing zone exist to
survive. Unverified on this machine, like the rest of the Windows path.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from fantabot.adapters.files.stopflag import (
    EXIT,
    PRECLEARED_ENV,
    clear_stop,
    request_stop,
    stop_path,
)

from fantabot_app.api.infrastructure.jobs import BufferingReporter

#: How long a stop waits for the child to go before it stops asking. §6 of the archived
#: fantalab-in-the-app-phase spec.
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
        role: str | None = None,
        landing: Path | None = None,
        flag: Path | None = None,
        grace_s: float = STOP_GRACE_S,
        poll_s: float = STOP_POLL_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Either a landing zone and its role, or a stop flag of its own — never neither.

        `landing` used to be doing three jobs at once: the single-holder **lock**
        (`role_lock`), the **address** of the stop flag (`stop_path(landing, role)`), and
        the thing `stop` **waits** on. A harvest job needs all three. An `asta` job has no
        landing zone and no role — and `lock_path` refuses any role but `collector` and
        `loader` — so it could not be built into anything that started. What it does need
        is the flag: its first stop is the *disarm*, and lock-free must not mean
        unstoppable.

        So the three are separated rather than `ROLES` widened. `lock.py`'s two roles are
        a contract about landing zones on the collection path, and giving it a third,
        unrelated one would change what "one holder per landing zone" means for the
        collector.

        A lock-free job's `flag` is its identity: one job per flag, which is the caller's
        to keep, exactly as one holder per (landing zone, role) is the kernel's.
        """
        if landing is not None and role is None:
            raise ValueError(
                "a landing zone needs its role — `collector` or `loader` — to be locked"
            )
        if role is not None and landing is None:
            raise ValueError(f"role {role!r} is a lock on a landing zone, and none was given")
        if landing is not None and flag is not None:
            raise ValueError(
                "a landing-zone job's stop flag is derived from the landing zone, because "
                "the child derives the same path — naming another would address a flag "
                "the child never reads"
            )
        self.command = command
        self.role = role
        self.landing = landing
        #: `(landing, role)` when there is a lock to take and to wait on, else `None`.
        self._owner = (landing, role) if landing is not None and role is not None else None
        if flag is not None:
            self.flag = flag
        elif self._owner is not None:
            self.flag = stop_path(*self._owner)
        else:
            raise ValueError(
                "a job with no landing zone must name its stop flag: without one there is "
                "no stop at all on Windows, where no signal is sent, and an acting job "
                "that cannot be stopped makes its disarm control a lie"
            )
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
        with self._holding_the_role():
            # Cleared here, holding the lock, *before* the child exists — which is what
            # makes this a happens-before rather than a shorter race. `start` returns the
            # moment `Popen` does and the UI renders Stop on that response, but the child
            # needs a fifth of a second to boot; a click in that window used to be written
            # and then unlinked unread by the child's own startup clear. Now the slate is
            # clean before there is anything to click Stop on, so every click after this
            # point is a request the child will see.
            #
            # It also still removes what a `SIGKILL`ed predecessor left: stage two's kill
            # runs no `finally`, so the flag survives holding `exit`.
            clear_stop(self.flag)

        with self._lock:
            # An argv list, never a shell: nothing here is composed from user input.
            self._process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # line-buffered: a job that buffers until exit looks hung
                creationflags=self._creation_flags(),
                # Told, not inferred: the child must not clear the flag again, or it
                # reopens the window this just closed. A terminal run sees no such
                # variable and keeps clearing, which is where the staleness guard still
                # has to live.
                env={**os.environ, PRECLEARED_ENV: "1"},
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
        """Flag, then `SIGINT`, then wait on the role lock, then `SIGKILL`.

        **Safe to call twice, and the second call means more than the first.** The flag
        escalates: *disarm*, then *exit*. That is deliberate rather than incidental — it
        is the terminal's two-Ctrl-C gesture, reproduced for a caller that is an HTTP
        request rather than a keyboard. Everything after the flag is idempotent, so a
        second stop costs one more signal and nothing else.

        The escalation state lives in the flag file, not in this object, so it survives
        the app restarting between an operator's two clicks — and so a browser tab that
        reloaded does not silently rewind the gesture to stage one.

        **Only *exit* waits, and only *exit* escalates.** *Disarm* does not mean "die":
        `asta bid` disarmed keeps drawing the room, which is the whole reason the gesture
        has two stages. A grace timer started at stage one would `SIGKILL` a child that is
        doing exactly what it was asked. So stage one asks and returns; stage two waits
        the grace and then kills.

        A collector still stops on the first click, and that is the child's decision, not
        this method's: it has nothing to disarm and winds down on either stage, the same
        way its first Ctrl-C already ends it.

        The wait watches the **role lock**, not only the exit status. The lock is what a
        second start consults, so releasing it is the event that matters; and the kernel
        releases it however the holder dies, which a pid check cannot promise.
        """
        process = self._process
        if process is None or process.poll() is not None:
            return

        reporter_pid = process.pid
        stage = self._request_stop(process)
        self._signal(process)
        if stage != EXIT:
            return

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

        Nothing sends into that group any more — see `_signal` — but the flag is kept:
        it is also what stops a console `Ctrl-C` at the app from propagating into a
        collector. Unverified on this machine; see the module docstring.
        """
        if os.name == "nt":  # pragma: no cover - POSIX here
            return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        return 0

    def _request_stop(self, process: subprocess.Popen[str]) -> str:
        """Write the flag for this (landing zone, role), and say which stage.

        Addressed by role rather than by pid. The first version named the child's
        `Popen.pid` and it did not work on Windows: the supervisor wrote `disarm` for pid
        2300 and the child polling that same file never matched it (`app-ci` run
        34112470790, whose job log holds `polling` and `stopping: disarm flag for pid
        2300` and no `saw disarm`). Whether `Popen.pid` and the child's own `os.getpid()`
        are the same number across a spawn from a uv venv there was never established.

        The pid was standing in for an identity `lock.py` already guarantees — one holder
        per (landing zone, role) — so the flag borrows the lock's own name instead, and
        the child clears it as it starts rather than checking who it was for.
        """
        # Annotated because `fantabot` ships no `py.typed`, so this venv's mypy reads
        # every symbol from it as `Any` and a bare `return` here is `Any` out of a `str`
        # function. The marker is the real fix and is a change of its own.
        stage: str = request_stop(self.flag)
        self._print(f"stopping: {stage} flag for pid {process.pid}")
        return stage

    def _signal(self, process: subprocess.Popen[str]) -> None:
        """SIGINT on POSIX, nothing on Windows — where the flag above is the whole stop.

        The `CTRL_BREAK_EVENT` that used to be here did not work: it reaches a Python
        child as SIGBREAK and terminates it before `except KeyboardInterrupt` runs, so the
        child's own shutdown never ran and the SIGKILL escalation behind it was
        unreachable. Sending nothing is not a regression from that; it is the removal of
        something that only ever looked like a stop.
        """
        if os.name == "nt":  # pragma: no cover - POSIX here
            return
        self._print(f"stopping: SIGINT to pid {process.pid}")
        process.send_signal(signal.SIGINT)

    # -- helpers ----------------------------------------------------------------------

    def _holding_the_role(self) -> AbstractContextManager[object]:
        """The role lock for a landing-zone job; nothing for a lock-free one.

        Held only across the pre-spawn clear, for the reason `start` gives. A lock-free job
        still gets the clear — the happens-before is about the flag, not the lock.
        """
        if self._owner is None:
            return nullcontext()
        from fantabot.adapters.files.lock import role_lock

        # Annotated, not returned bare: `fantabot` ships no `py.typed`, so mypy reads
        # `role_lock` as `Any` — the reason `_request_stop` annotates its `stage` too.
        held: AbstractContextManager[object] = role_lock(*self._owner)
        return held

    def _role_is_free(self) -> bool:
        """Whether `stop` may stop waiting. Always, for a job that holds no role.

        With no lock to watch, the exit status is the whole wait. Asking a lock-free job
        for a lock would spin out the grace and `SIGKILL` a child that had already left
        cleanly — "announced, never silent", and wrong.
        """
        if self._owner is None:
            return True
        from fantabot.adapters.files.lock import RoleBusy, role_lock

        try:
            with role_lock(*self._owner):
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
