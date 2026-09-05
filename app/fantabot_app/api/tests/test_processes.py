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

import signal
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
    """A job that buffers until exit is indistinguishable from a hung one."""
    reporter = BufferingReporter()
    job = ProcessJob(_python(POLITE), role="loader", landing=tmp_path / "live.jsonl")
    _run_in_thread(job, reporter)

    assert _wait(lambda: "first" in reporter.lines), reporter.lines
    assert job.running

    job.stop()
    assert _wait(lambda: not job.running)


def test_stop_sends_sigint_and_the_child_exits_cleanly(tmp_path: Path) -> None:
    reporter = BufferingReporter()
    job = ProcessJob(_python(POLITE), role="loader", landing=tmp_path / "live.jsonl")
    _run_in_thread(job, reporter)
    assert _wait(lambda: "first" in reporter.lines)

    job.stop()

    assert _wait(lambda: not job.running)
    assert "interrupted" in reporter.lines, reporter.lines
    assert any("SIGINT" in line for line in reporter.lines), reporter.lines
    assert job.returncode == 0


def test_a_child_that_ignores_sigint_is_killed_and_the_log_says_so(tmp_path: Path) -> None:
    """The escalation is visible rather than silent: a job that was killed and one that
    stopped politely are the same row otherwise, and only one of them lost work."""
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

    assert _wait(lambda: not job.running)
    assert any("SIGKILL" in line for line in reporter.lines), reporter.lines
    assert job.returncode == -signal.SIGKILL


def test_stop_polls_the_role_lock_rather_than_sleeping_out_the_grace(tmp_path: Path) -> None:
    """15 s is the bound, not the cost. A stop that always paid it would make the button
    feel broken on the one evening it is used."""
    reporter = BufferingReporter()
    job = ProcessJob(
        _python(POLITE), role="loader", landing=tmp_path / "live.jsonl", grace_s=15.0
    )
    _run_in_thread(job, reporter)
    assert _wait(lambda: "first" in reporter.lines)

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
