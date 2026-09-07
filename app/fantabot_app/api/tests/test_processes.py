"""The process supervisor: a real child, its stdout, and the stop sequence.

A subprocess and not a thread, and §6 of the archived phase spec says why
(`tasks/archive/fantalab-in-the-app-spec.md`). `adapters/files/landing.py`
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


def _wait(predicate, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _run_in_thread(job: ProcessJob, reporter: BufferingReporter) -> object:
    import threading

    done: list[bool] = []

    def run() -> None:
        job.run(reporter)
        done.append(True)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return done


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
    job = ProcessJob(_python(FLAG_POLLER) + [str(landing)], role="loader", landing=landing)
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
        _python(FLAG_POLLER) + [str(landing)], role="loader", landing=landing, grace_s=15.0
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
        _python(FLAG_POLLER) + [str(landing)],
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
        _python(FLAG_POLLER) + [str(landing)], role="loader", landing=landing, grace_s=0.4
    )
    _run_in_thread(job, reporter)
    assert _wait(lambda: "polling" in reporter.lines)

    job.stop()
    assert _wait(lambda: "saw disarm" in reporter.lines), reporter.lines

    assert any("disarm" in line and "stopping" in line for line in reporter.lines), reporter.lines


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
        _python(FLAG_POLLER) + [str(landing)], role="loader", landing=landing, grace_s=0.4
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


def test_a_stop_clicked_before_the_child_boots_is_not_lost(tmp_path: Path) -> None:
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
    """
    landing = tmp_path / "live.jsonl"
    reporter = BufferingReporter()
    job = ProcessJob(
        _python(SLOW_FLAG_POLLER) + [str(landing)],
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
    job = ProcessJob(_python(FLAG_POLLER) + [str(landing)], role="loader", landing=landing)
    _run_in_thread(job, reporter)
    assert _wait(lambda: "polling" in reporter.lines), reporter.lines

    assert read_stop(flag) is None, "start() must clear before it spawns"

    job.stop()
    job.stop()
    assert _wait(lambda: not job.running)
