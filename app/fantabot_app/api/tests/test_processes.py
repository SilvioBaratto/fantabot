"""The process supervisor: a real child, its stdout, and the stop sequence.

A subprocess and not a thread, and §6 of the archived fantalab-in-the-app-phase spec
says why. `adapters/files/landing.py`
states the invariant the choice protects: a frame that never reached disk is gone, and an
evening of auctions does not come back. A daemon thread dies with the server, and
`jobs.py` accepts that on the grounds that every fantabot write is an upsert — true of
the loader, **false of the collector**. `harvest_loader.py` also records a pass holding
~17x its 32 MB window (1.6 GB resident measured), which inside the SPA process means the
UI stalls on a timer.

These drive **real** children. A mock cannot fail the way this must not: the whole point
of the stop sequence is what the operating system does with a signal and a lock, and a
fake `Popen` would agree with whatever the implementation happened to do.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fantabot_app.api.infrastructure.jobs import BufferingReporter
from fantabot_app.api.infrastructure.processes import (
    ProcessJob,
    fantabot_command,
)

from .conftest import wait_for as _wait


def _python(script: str) -> list[str]:
    return [sys.executable, "-c", script]


#: Prints, flushes, then sits still. Exits 0 on SIGINT, like the CLI's own commands.
POLITE = """
import sys, time
print("first", flush=True)
try:
    time.sleep(60)
except KeyboardInterrupt:
    print("interrupted", flush=True)
    sys.exit(0)
"""

#: Ignores SIGINT entirely. The only thing that ends it is SIGKILL.
STUBBORN = """
import signal, sys, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
print("ignoring", flush=True)
sys.stdout.flush()
while True:
    time.sleep(0.05)
"""

#: Ignores SIGINT and polls the stop flag instead — a stand-in for what `harvest collect`
#: does, and the only child in this file that can be stopped on Windows. It says which
#: stage it was asked for, so a test can tell the two apart without reading the file
#: itself.
#:
#: It clears the flag before announcing itself **through the same conditional the real
#: commands use** — `clear_unless_precleared`. That matters: a supervised child must not
#: clear, because `ProcessJob.start` already did it before the child existed, and clearing
#: again would erase a click made while this was still booting.
#:
#: `saw disarm` is printed once rather than every 50 ms — the loop would otherwise flood
#: the job log with twenty lines a second while disarmed, which is the state a room sits
#: in for as long as the operator wants to keep watching it.
#: The same child, but slow to reach its own startup code — which is what the real
#: commands are. `harvest collect` spends 0.13-0.32 s warm on interpreter boot, lazy
#: imports, the seed parse and the role lock before it ever touches the flag; this stub
#: reaches it in 0.03 s, which is fast enough to hide the startup window entirely. The
#: sleep is not padding: without it the window test passes for the wrong reason.
FLAG_POLLER = """
import signal, sys, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
from pathlib import Path
from fantabot.adapters.files.stopflag import clear_unless_precleared, read_stop, stop_path
flag = stop_path(Path(sys.argv[1]), "loader")
clear_unless_precleared(flag)
print("polling", flush=True)
seen = None
while True:
    state = read_stop(flag)
    if state is not None and state != seen:
        seen = state
        print("saw " + state, flush=True)
        if state == "exit":
            sys.exit(0)
    time.sleep(0.05)
"""


def _run_in_thread(job: ProcessJob, reporter: BufferingReporter) -> object:
    import threading

    done: list[bool] = []

    def run() -> None:
        job.run(reporter)
        done.append(True)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return done


@pytest.fixture(autouse=True)
def _no_child_outlives_its_test():
    """Kill anything this file spawned that is still alive when the test ends.

    **Not belt-and-braces: a test here that forgets stage two leaks a process that cannot
    be signalled.** `FLAG_POLLER`'s second line is `signal.signal(SIGINT, SIG_IGN)` — it is
    supposed to be, that is the child the two-stage stop exists for — so a test that sends
    only stage one leaves a poller running at 20 Hz for ever, reparented to init, polling a
    `tmp_path` pytest has already deleted. On 2026-09-21 there were **151** of them, the
    oldest two days old, together burning 15.6% of a core, all from the one test below that
    stopped once.

    Nothing announced it: the test passed, the suite passed, and `tmp_path` cleanup does not
    touch processes. So the guard is here rather than in that one test — the next test
    written the same way would leak in the same silence.

    It wraps `Popen` rather than tracking `ProcessJob`s, because what has to die is the
    child, and a job that failed before assigning `self._process` would have no handle to
    offer.
    """
    import subprocess as _sp

    spawned: list[_sp.Popen] = []
    real = _sp.Popen

    class _Tracked(real):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            spawned.append(self)

    _sp.Popen = _Tracked  # type: ignore[misc]
    try:
        yield
    finally:
        _sp.Popen = real  # type: ignore[misc]
        for child in spawned:
            if child.poll() is not None:
                continue
            child.kill()
            # `wait` is not wrapped: a child that survives SIGKILL for five seconds is
            # either uninterruptibly blocked or not ours, and `TimeoutExpired` reaching the
            # test is the point. Swallowing it would restore the quiet leak this exists for.
            child.wait(timeout=5)


def test_the_command_names_the_interpreter_and_the_module_not_the_console_script() -> None:
    """`fantabot` is on `PATH` only while the CLI's venv is active; the app's is another.

    `sys.executable -m fantabot` needs nothing on `PATH` at all, which is what
    `src/fantabot/__main__.py` exists for.
    """
    assert fantabot_command("harvest", "load", "--follow") == [
        sys.executable,
        "-m",
        "fantabot",
        "harvest",
        "load",
        "--follow",
    ]


def test_python_dash_m_fantabot_actually_runs() -> None:
    """The one thing a unit test cannot assert about `__main__.py`: that it is reachable."""
    result = subprocess.run(
        [sys.executable, "-m", "fantabot", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout


def test_stdout_reaches_the_log_while_the_child_is_still_running(tmp_path: Path) -> None:
    """A job that buffers until exit is indistinguishable from a hung one.

    Driven by the flag-polling child rather than the SIGINT-only one so the teardown
    works on every platform: on Windows stage one sends no signal at all, and a POLITE
    child would still be running when the test ended.
    """
    landing = tmp_path / "live.jsonl"
    reporter = BufferingReporter()
    job = ProcessJob([*_python(FLAG_POLLER), str(landing)], role="loader", landing=landing)
    _run_in_thread(job, reporter)

    assert _wait(lambda: "polling" in reporter.lines), reporter.lines
    assert job.running

    job.stop()
    job.stop()
    assert _wait(lambda: not job.running)


@pytest.mark.skipif(os.name == "nt", reason="SIGINT is the POSIX half of the stop")
def test_stop_sends_sigint_and_the_child_exits_cleanly(tmp_path: Path) -> None:
    """The POSIX half, kept and kept honest about being one half.

    `SIGINT` is still sent, still not `SIGTERM` — `aste_collect` catches only
    `KeyboardInterrupt` — and it is what ends a child that predates the flag. It is not
    the mechanism the two-stage tests rely on: those drive a child that ignores it, so
    nothing there can be passing because of this.

    Skipped rather than rewritten on Windows because there is no equivalent to assert.
    `CTRL_BREAK_EVENT` was the equivalent and it killed the child before its own handler
    ran, which is the reason the flag exists.
    """
    reporter = BufferingReporter()
    job = ProcessJob(_python(POLITE), role="loader", landing=tmp_path / "live.jsonl")
    _run_in_thread(job, reporter)
    assert _wait(lambda: "first" in reporter.lines)

    job.stop()

    assert _wait(lambda: not job.running)
    assert "interrupted" in reporter.lines, reporter.lines
    assert any("SIGINT" in line for line in reporter.lines), reporter.lines
    assert job.returncode == 0


def test_a_child_that_ignores_everything_is_killed_and_the_log_says_so(tmp_path: Path) -> None:
    """The escalation is visible rather than silent: a job that was killed and one that
    stopped politely are the same row otherwise, and only one of them lost work.

    **Two stops, not one, and that is the contract change.** The kill is stage two's, not
    stage one's: *disarm* does not mean "die", and a grace timer started at stage one
    would kill a child doing exactly what it was asked — `asta bid` disarmed keeps
    drawing. This child ignores both the signal and the flag, which is the only way to
    reach the escalation at all.

    The return code is asserted as "not clean" rather than as `-SIGKILL`. On Windows
    `Popen.kill` is `TerminateProcess` and there is no negative signal number to compare
    against, so the old form failed there for a reason that had nothing to do with the
    escalation working. What is platform-independent is our own log line, and that is
    what says a kill happened.
    """
    reporter = BufferingReporter()
    job = ProcessJob(
        _python(STUBBORN),
        role="loader",
        landing=tmp_path / "live.jsonl",
        grace_s=0.5,
        poll_s=0.05,
    )
    _run_in_thread(job, reporter)
    assert _wait(lambda: "ignoring" in reporter.lines), reporter.lines

    job.stop()
    assert job.running, "stage one asks; it does not kill"

    job.stop()

    assert _wait(lambda: not job.running)
    assert any("SIGKILL" in line for line in reporter.lines), reporter.lines
    assert job.returncode != 0


def test_stop_polls_the_role_lock_rather_than_sleeping_out_the_grace(tmp_path: Path) -> None:
    """15 s is the bound, not the cost. A stop that always paid it would make the button
    feel broken on the one evening it is used.

    Timed around the **second** stop, because that is the only one that waits: stage one
    asks and returns, so timing it would measure nothing. The child exits on *exit*, so a
    `stop()` that slept out its grace instead of watching the lock would take 15 s.
    """
    landing = tmp_path / "live.jsonl"
    reporter = BufferingReporter()
    job = ProcessJob(
        [*_python(FLAG_POLLER), str(landing)], role="loader", landing=landing, grace_s=15.0
    )
    _run_in_thread(job, reporter)
    assert _wait(lambda: "polling" in reporter.lines)
    job.stop()
    assert _wait(lambda: "saw disarm" in reporter.lines), reporter.lines

    started = time.monotonic()
    job.stop()
    assert _wait(lambda: not job.running)

    assert time.monotonic() - started < 5.0


def test_a_second_start_refuses_while_the_role_lock_is_held(tmp_path: Path) -> None:
    """Killing the app leaves the child running — it is a subprocess. The next start must
    find it through the OS, not through state the dead process was holding."""
    from fantabot.adapters.files.lock import RoleBusy, role_lock

    landing = tmp_path / "live.jsonl"
    with role_lock(landing, "loader"):
        job = ProcessJob(_python(POLITE), role="loader", landing=landing)

        with pytest.raises(RoleBusy) as caught:
            job.start()

    assert caught.value.role == "loader"
    assert not job.running


def test_a_non_zero_exit_is_reported_as_a_failed_job(tmp_path: Path) -> None:
    reporter = BufferingReporter()
    job = ProcessJob(
        _python("import sys; print('nope', flush=True); sys.exit(2)"),
        role="loader",
        landing=tmp_path / "live.jsonl",
    )

    assert job.run(reporter) is False
    assert job.returncode == 2
    assert "nope" in reporter.lines


def test_stopping_a_job_that_never_started_is_a_no_op(tmp_path: Path) -> None:
    job = ProcessJob(_python(POLITE), role="loader", landing=tmp_path / "live.jsonl")

    job.stop()  # must not raise

    assert not job.running


# -- the cooperative stop ---------------------------------------------------------------


def test_the_first_stop_disarms_and_the_second_exits(tmp_path: Path) -> None:
    """The two-stage gesture, proven end to end against a real child and **without
    naming SIGKILL** — spec criterion 2.

    This child ignores SIGINT, so nothing here can be passing because of the POSIX
    signal: the flag is the only thing reaching it. That is the same position every
    supervised child is in on Windows, where `CTRL_BREAK_EVENT` arrives as SIGBREAK and
    kills the interpreter before its `except KeyboardInterrupt` runs (`exited
    3221225786`, the runner's own log). Making the assertion platform-independent is the
    point: the mechanism this proves is the one that runs everywhere.
    """
    landing = tmp_path / "live.jsonl"
    reporter = BufferingReporter()
    job = ProcessJob(
        [*_python(FLAG_POLLER), str(landing)],
        role="loader",
        landing=landing,
        grace_s=0.4,
        poll_s=0.05,
    )
    _run_in_thread(job, reporter)
    assert _wait(lambda: "polling" in reporter.lines), reporter.lines

    job.stop()

    assert _wait(lambda: "saw disarm" in reporter.lines), reporter.lines
    assert job.running, "disarm is not exit: a disarmed run keeps drawing"

    job.stop()

    assert _wait(lambda: not job.running), reporter.lines
    assert "saw exit" in reporter.lines, reporter.lines
    assert job.returncode == 0, "the child ran its own shutdown, it was not killed"


def test_the_stop_is_announced_as_a_flag_write(tmp_path: Path) -> None:
    """A stop that says only "SIGINT" would be describing the half that does not work on
    the platform this was built for."""
    landing = tmp_path / "live.jsonl"
    reporter = BufferingReporter()
    job = ProcessJob(
        [*_python(FLAG_POLLER), str(landing)], role="loader", landing=landing, grace_s=0.4
    )
    _run_in_thread(job, reporter)
    assert _wait(lambda: "polling" in reporter.lines)

    job.stop()
    assert _wait(lambda: "saw disarm" in reporter.lines), reporter.lines

    assert any("disarm" in line and "stopping" in line for line in reporter.lines), reporter.lines

    # Stage two, and it is not tidiness. This test is about what stage *one* writes, so it
    # used to stop once and return — leaving a child that had been asked to disarm and never
    # asked to exit. `FLAG_POLLER` exits only on `exit` and ignores `SIGINT` by design, so
    # that child polled at 20 Hz for ever. Every sibling in this file already stops twice;
    # this was the only one that did not, and it accounted for all 151 orphans found on
    # 2026-09-21. The autouse reaper above would now catch it, which is why this line is
    # about the *contract* — a stop that stops is two stops — rather than about cleanup.
    job.stop()
    assert _wait(lambda: not job.running), reporter.lines


def test_the_flag_is_named_for_the_role_and_holds_only_the_stage(tmp_path: Path) -> None:
    """The addressee is the (landing zone, role) the role lock already makes unique — not
    a pid.

    Two collectors cannot coexist on one landing zone and neither can two loaders, so
    "whoever holds this role here" names exactly one process; and a collector *and* a
    loader on one landing zone is the intended pairing, which is why the role is in the
    file name rather than only in the lock's.

    The pid version of this is what run 34112470790 disproved: the supervisor wrote for
    pid 2300 and the child polling that file never matched.
    """
    import json

    from fantabot.adapters.files.stopflag import stop_path

    landing = tmp_path / "live.jsonl"
    reporter = BufferingReporter()
    job = ProcessJob(
        [*_python(FLAG_POLLER), str(landing)], role="loader", landing=landing, grace_s=0.4
    )
    _run_in_thread(job, reporter)
    assert _wait(lambda: "polling" in reporter.lines)

    job.stop()
    assert _wait(lambda: "saw disarm" in reporter.lines), reporter.lines

    flag = stop_path(landing, "loader")
    assert flag.name == "live.jsonl.loader.stop"
    assert json.loads(flag.read_text(encoding="utf-8")) == {"state": "disarm"}
    assert stop_path(landing, "collector") != flag

    job.stop()
    assert _wait(lambda: not job.running)


#: `FLAG_POLLER`, delayed. Spliced rather than duplicated so the two cannot drift.
SLOW_FLAG_POLLER = FLAG_POLLER.replace(
    'print("polling", flush=True)', 'time.sleep(1.0)\nprint("polling", flush=True)'
).replace("clear_stop(flag)", "time.sleep(1.0)\nclear_stop(flag)")


def test_a_stop_clicked_before_the_child_boots_is_not_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The startup window, closed.

    `start()` returns the moment `Popen` does and the UI renders Stop on that response —
    but the child needs a fifth of a second to boot Python, run its lazy imports and parse
    its seed. A click inside that window used to be written, logged, and then **unlinked
    unread** by the child's own startup clear. On POSIX the SIGINT covered it; on Windows,
    where no signal is sent, the click was simply lost and the operator clicked again.

    `stopflag.py`'s docstring claimed the opposite — *"no request written before it began
    was written for it"* — which is exactly false for a supervisor that has just spawned
    the child.

    The fix is a real happens-before, not a shorter window: `start()` clears the flag
    **while it holds the role lock, before `Popen`**, so the slate is clean before the
    child exists; and it tells the child so, which is why the child must not clear again.

    This test stops the job with no delay at all — the child is still booting — and
    requires the request to survive.

    **It drives the no-signal path, and that is the point rather than a convenience.**
    `_signal` sends SIGINT on POSIX and nothing on Windows, where the flag *is* the whole
    stop — and Windows is where the click was lost, which is what 0.13 fixed. Leaving the
    SIGINT in made the test a coin flip: `FLAG_POLLER` installs `SIG_IGN` on its second line,
    so a signal arriving during interpreter startup killed the child before it could ignore
    it (`site: Failed to import the site module` / `Python runtime state: initialized`).
    Measured at `a442499`: 2 failures in 6 whole-file runs, and never in isolation. The
    signal was masking the property, not exercising it.

    **The guard still bites, and it bites on whole-file runs.** With `start`'s
    `PRECLEARED_ENV` removed, this fails 2 of 3 whole-file runs and passes every time under
    `-k`. So the single-test run is the unreliable measurement here, not the file — which is
    the same trap the original flake set, in the other direction. Verified both ways:
    10 of 10 whole-file runs green with the fix, 2 of 3 red without it.
    """
    monkeypatch.setattr(ProcessJob, "_signal", lambda _self, _process: None)

    landing = tmp_path / "live.jsonl"
    reporter = BufferingReporter()
    job = ProcessJob(
        [*_python(SLOW_FLAG_POLLER), str(landing)],
        role="loader",
        landing=landing,
        grace_s=0.4,
        poll_s=0.05,
    )
    _run_in_thread(job, reporter)
    assert _wait(lambda: job.pid is not None), "the child never spawned"

    job.stop()  # no wait: the child has not reached its own startup code yet

    assert _wait(lambda: "saw disarm" in reporter.lines, timeout=20.0), reporter.lines

    job.stop()
    assert _wait(lambda: not job.running), reporter.lines


def test_start_clears_a_flag_left_by_a_killed_predecessor(tmp_path: Path) -> None:
    """The other half of moving the clear to the supervisor: it must still remove a stale
    flag, or the escalation path plants one that never goes away.

    A child ended by `SIGKILL` — which stage two does — never runs its own `finally`, so
    the flag survives holding `exit`. Without this the next run's *first* click would read
    that stale exit and escalate immediately.
    """
    from fantabot.adapters.files.stopflag import read_stop, request_stop, stop_path

    landing = tmp_path / "live.jsonl"
    flag = stop_path(landing, "loader")
    request_stop(flag)
    request_stop(flag)
    assert read_stop(flag) == "exit", "a killed predecessor left the flag at exit"

    reporter = BufferingReporter()
    job = ProcessJob([*_python(FLAG_POLLER), str(landing)], role="loader", landing=landing)
    _run_in_thread(job, reporter)
    assert _wait(lambda: "polling" in reporter.lines), reporter.lines

    assert read_stop(flag) is None, "start() must clear before it spawns"

    job.stop()
    job.stop()
    assert _wait(lambda: not job.running)


# -- a job with no landing zone (3.5) ----------------------------------------------------

#: `FLAG_POLLER`, addressed by a flag **path** rather than a (landing zone, role) pair —
#: which is all a job with no landing zone has. An `asta bid` has no landing zone and no
#: role, and its first Ctrl-C is the disarm the whole acting path rests on, so a lock-free
#: job must still be stoppable through its flag, in both stages.
FLAG_PATH_POLLER = """
import signal, sys, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
from pathlib import Path
from fantabot.adapters.files.stopflag import clear_unless_precleared, read_stop
flag = Path(sys.argv[1])
clear_unless_precleared(flag)
print("polling", flush=True)
seen = None
while True:
    state = read_stop(flag)
    if state is not None and state != seen:
        seen = state
        print("saw " + state, flush=True)
        if state == "exit":
            sys.exit(0)
    time.sleep(0.05)
"""


def test_a_job_with_no_landing_zone_starts_and_runs(tmp_path: Path) -> None:
    """`start` took `role_lock(self.landing, self.role)` unconditionally, and `lock_path`
    raises `ValueError` for any role but `collector` and `loader`. So a job with neither —
    the shape every `asta` job has — could not be constructed into anything that started.
    """
    reporter = BufferingReporter()
    job = ProcessJob(
        _python("print('hello', flush=True)"), flag=tmp_path / "asta.stop"
    )

    assert job.run(reporter) is True
    assert "hello" in reporter.lines


def test_a_job_with_no_landing_zone_disarms_then_exits(tmp_path: Path) -> None:
    """Lock-free is not unstoppable. The two-stage gesture, through the flag alone.

    This child ignores SIGINT, so the flag is the only thing reaching it — the position
    every supervised child is in on Windows. And `stop`'s second stage must still return
    once the child exits: with no role lock to watch, the exit status is the whole wait,
    and a wait that asked for a lock nobody holds would spin out the grace and `SIGKILL`
    a child that had already left cleanly.
    """
    flag = tmp_path / "asta.stop"
    reporter = BufferingReporter()
    job = ProcessJob(
        [*_python(FLAG_PATH_POLLER), str(flag)], flag=flag, grace_s=5.0, poll_s=0.05
    )
    _run_in_thread(job, reporter)
    assert _wait(lambda: "polling" in reporter.lines), reporter.lines

    job.stop()

    assert _wait(lambda: "saw disarm" in reporter.lines), reporter.lines
    assert job.running, "disarm is not exit: a disarmed run keeps drawing"

    job.stop()

    assert _wait(lambda: not job.running), reporter.lines
    assert job.returncode == 0, "the child ran its own shutdown, it was not killed"
    assert not any("SIGKILL" in line for line in reporter.lines), reporter.lines


def test_a_job_with_no_landing_zone_clears_its_flag_before_spawning(tmp_path: Path) -> None:
    """The happens-before `start` gives a landing-zone job, kept for a lock-free one.

    A killed predecessor's `exit` would otherwise make the next run's first click an exit,
    landing the old run's second gesture on a process that never saw the first.
    """
    from fantabot.adapters.files.stopflag import read_stop, request_stop

    flag = tmp_path / "asta.stop"
    request_stop(flag)
    request_stop(flag)
    assert read_stop(flag) == "exit", "a killed predecessor left the flag at exit"

    job = ProcessJob(_python(POLITE), flag=flag)
    job.start()
    try:
        assert read_stop(flag) is None, "start() must clear before it spawns"
    finally:
        job.stop()
        job.stop()


def test_a_job_with_no_landing_zone_lists_and_stops_like_any_other(tmp_path: Path) -> None:
    """The registry is indifferent to how a job is supervised, which is the point: the UI
    polls `GET /jobs` and cannot tell which kind it is watching."""
    from fantabot_app.api.infrastructure.jobs import JobRegistry

    flag = tmp_path / "asta.stop"
    registry = JobRegistry()
    job = ProcessJob([*_python(FLAG_PATH_POLLER), str(flag)], flag=flag, poll_s=0.05)
    job_id = registry.start(job.run, kind="asta-watch", stop=job.stop)
    assert _wait(lambda: job.running)

    [summary] = [s for s in registry.list() if s.id == job_id]
    assert summary.kind == "asta-watch"
    assert summary.stoppable is True

    assert registry.stop(job_id) is True
    assert registry.stop(job_id) is True
    assert _wait(lambda: not job.running)


def test_a_landing_zone_needs_its_role_and_a_lock_free_job_needs_its_flag(
    tmp_path: Path,
) -> None:
    """Refused at construction, where the mistake is made, not at the first stop click.

    A landing zone without a role cannot be locked and names no flag. A job with neither a
    landing zone nor a flag would have **no stop at all on Windows** — no signal is sent
    there — and an acting job that cannot be stopped makes its disarm control a lie.
    """
    with pytest.raises(ValueError, match="role"):
        ProcessJob(_python(POLITE), landing=tmp_path / "live.jsonl")
    with pytest.raises(ValueError, match="landing"):
        ProcessJob(_python(POLITE), role="loader")
    with pytest.raises(ValueError, match="flag"):
        ProcessJob(_python(POLITE))
