"""A child in its own process group, and the group taken down together.

The refresh runs as a child because it imports the agent SDK and the whole persistence
stack, talks to a live site and can hang in a way no `try` catches — and the submit must
not. What this file pins is the part that goes wrong silently: a hung child killed on its
own leaves *its* children running, and the parent exits reporting success.

⚠ **Every test here reaps what it starts.** This repository has already paid for the other
kind once: a SIGINT-ignoring child left 151 processes behind, because pytest reaps
directories and not processes. The two tests that start a real child assert the group is
gone afterwards and kill it themselves if it is not.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from fantabot.adapters.process import group_alive, run_grouped

#: Sleeps forever and **ignores SIGTERM**. The child that makes SIGKILL necessary.
STUBBORN = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('up', flush=True)\n"
    "time.sleep(600)\n"
)
#: Sleeps forever and takes SIGTERM. The polite one.
PATIENT = "import time\ntime.sleep(600)\n"


@pytest.fixture
def no_strays() -> Iterator[list[int]]:
    """Whatever a test starts, this ends — by pid **and** by group.

    The fixture is the reaping, not the assertion: a test that failed before its own kill
    landed must not leave the next run something to trip over. The first version of this
    file did, and the stray it left then failed the *following* run for the wrong reason.
    """
    started: list[int] = []
    yield started
    for pid in started:
        # ⚠ **Checked before it is signalled, and never by group.** A pid this suite has
        # already reaped is free to be recycled, and `killpg` on a stale pgid would signal a
        # stranger's whole session — so only a process still alive is signalled, and only
        # that one process. The group is `run_grouped`'s job; this is the net under it.
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue


class TestTheHappyPath:
    def test_a_child_that_finishes_returns_its_code(self) -> None:
        code, note = run_grouped([sys.executable, "-c", "raise SystemExit(0)"], timeout=30)

        assert (code, note) == (0, "")

    def test_a_failing_child_returns_its_code_and_no_note(self) -> None:
        """A non-zero exit is the child's answer, not a failure of the runner: the caller
        decides what a failed refresh means, and here it means nothing at all."""
        code, note = run_grouped([sys.executable, "-c", "raise SystemExit(3)"], timeout=30)

        assert (code, note) == (3, "")

    def test_a_command_that_cannot_start_is_a_note_and_not_a_raise(self) -> None:
        """The caller is a job whose real work is already done and written down."""
        code, note = run_grouped(["/nonexistent/binary/fantabot"], timeout=5)

        assert code is None
        assert "could not start" in note


class TestTheTimeout:
    def test_a_child_that_overruns_is_killed_and_named(self) -> None:
        code, note = run_grouped([sys.executable, "-c", PATIENT], timeout=0.3, grace=2.0)

        assert code is None
        assert note.startswith("killed after")

    def test_a_child_that_ignores_sigterm_still_dies(self) -> None:
        code, note = run_grouped([sys.executable, "-c", STUBBORN], timeout=0.5, grace=0.5)

        assert code is None
        assert "SIGTERM ignored" in note

    def test_the_whole_group_is_gone_and_not_only_the_child(
        self, tmp_path: Path, no_strays: list[int]
    ) -> None:
        """The failure this exists for, and the one the first version had.

        A hung refresh has already spawned the voti scrape and the agent. If the escalation
        asks *the child* whether SIGTERM worked, a leader that dies on cue reports a clean
        kill while its grandchildren run on — measured 2026-09-23, one stray every run. So
        the child here dies politely and leaves behind a grandchild that ignores SIGTERM,
        and the assertion is on **that pid**, written to a file so it cannot be confused
        with a stray from some other test.
        """
        marker = tmp_path / "grandchild.pid"
        grandchild = (
            "import signal, os, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
            "time.sleep(600)\n"
        )
        script = (
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
            "time.sleep(600)\n"
        )

        code, note = run_grouped([sys.executable, "-c", script], timeout=1.0, grace=0.5)

        assert code is None and note
        deadline = time.monotonic() + 5.0
        while not marker.exists() and time.monotonic() < deadline:  # pragma: no cover
            time.sleep(0.05)
        assert marker.exists(), "the grandchild never started; the assertion below is blind"
        stray = int(marker.read_text())
        no_strays.append(stray)
        time.sleep(0.3)

        assert not _alive(stray), f"the grandchild {stray} outlived the group kill"


def _alive(pid: int) -> bool:
    """Signal 0 to one process. `group_alive` asks about a group; this asks about a pid."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


class TestTheGroupProbe:
    def test_a_living_group_reads_as_alive(self) -> None:
        # Not registered with `no_strays`: this test reaps its own child, and a pid the
        # suite has reaped is free to be recycled — the net must never signal one.
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True
        )
        try:
            assert group_alive(child.pid)
        finally:
            child.kill()
            child.wait()

    def test_a_dead_group_reads_as_dead(self) -> None:
        child = subprocess.Popen(
            [sys.executable, "-c", "raise SystemExit(0)"], start_new_session=True
        )
        child.wait()

        assert group_alive(child.pid) is False
