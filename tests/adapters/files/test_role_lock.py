"""One collector and one loader per landing zone — enforced by the OS, not by a pid file.

Two collectors against the same landing zone double every record, and two loaders corrupt
each other's ladders. Collector-plus-loader is the *intended* pairing, so this is a lock
per role rather than one lock per file.

**Why an OS advisory lock and not a pid file.** The property being bought is that the
kernel releases the lock however the holder dies — `SIGKILL`, a panic, the machine losing
power. A pid file records an intention and outlives the process that wrote it, so
"is a collector running?" becomes "is pid 40122 still a collector or is it now your
browser?". That question is unanswerable, and it is asked at 21:47 on an asta evening.

The kill case below uses a **real child process**. A mock cannot fail the way this must
not: `flock` releases on process death because the kernel closes the fd, and nothing about
that is visible from inside one interpreter.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fantabot.adapters.files.lock import COLLECTOR, LOADER, RoleBusy, role_lock


def _take(landing: Path, role: str) -> None:
    """Take the lock and release it at once — the whole of "can this role be had?"."""
    with role_lock(landing, role):
        pass


def test_the_two_roles_are_the_two_that_conflict() -> None:
    assert (COLLECTOR, LOADER) == ("collector", "loader")


def test_a_lock_is_taken_and_released(tmp_path: Path) -> None:
    landing = tmp_path / "live.jsonl"

    with role_lock(landing, COLLECTOR) as held:
        assert held.exists()

    with role_lock(landing, COLLECTOR):
        pass  # released, so it can be taken again


def test_a_second_holder_of_the_same_role_is_refused(tmp_path: Path) -> None:
    landing = tmp_path / "live.jsonl"

    with role_lock(landing, COLLECTOR), pytest.raises(RoleBusy) as busy:
        _take(landing, COLLECTOR)

    assert busy.value.role == COLLECTOR
    assert str(landing) in str(busy.value)
    assert COLLECTOR in str(busy.value)


def test_a_loader_runs_alongside_a_collector(tmp_path: Path) -> None:
    """The intended pairing: the collector appends and the loader reads behind it."""
    landing = tmp_path / "live.jsonl"

    with role_lock(landing, COLLECTOR), role_lock(landing, LOADER):
        pass


def test_two_landing_zones_do_not_share_a_lock(tmp_path: Path) -> None:
    with role_lock(tmp_path / "a.jsonl", COLLECTOR), role_lock(tmp_path / "b.jsonl", COLLECTOR):
        pass


def test_the_lock_file_sits_beside_the_landing_zone(tmp_path: Path) -> None:
    """Beside it, and named for the role, so `ls` answers "what is running here"."""
    landing = tmp_path / "live.jsonl"

    with role_lock(landing, COLLECTOR) as held:
        assert held == tmp_path / "live.jsonl.collector.lock"


def test_the_directory_is_created_if_it_is_not_there(tmp_path: Path) -> None:
    """A first collect into a fresh harvest home must not fail on the lock."""
    landing = tmp_path / "new" / "live.jsonl"

    with role_lock(landing, COLLECTOR) as held:
        assert held.parent.is_dir()


def test_an_unknown_role_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="watcher"):
        _take(tmp_path / "live.jsonl", "watcher")


HOLDER = """
import sys, time
from pathlib import Path
from fantabot.adapters.files.lock import role_lock

with role_lock(Path(sys.argv[1]), sys.argv[2]):
    print("held", flush=True)
    time.sleep(120)
"""


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX")
def test_the_lock_dies_with_the_process_that_held_it(tmp_path: Path) -> None:
    """SIGKILL — no handler runs, no `finally` executes, nothing is cleaned up.

    This is the whole reason the lock is the kernel's and not ours. A collector that is
    killed must leave a landing zone the next one can take, without an operator having to
    know that a `.lock` file is stale.
    """
    landing = tmp_path / "live.jsonl"
    child = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(landing), COLLECTOR],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held", "the child never took the lock"

        with pytest.raises(RoleBusy):
            _take(landing, COLLECTOR)

        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)
    finally:
        if child.poll() is None:  # pragma: no cover - only on a failed assertion above
            child.kill()
            child.wait(timeout=10)

    deadline = time.monotonic() + 5
    while True:
        try:
            _take(landing, COLLECTOR)
            return
        except RoleBusy:  # pragma: no cover - the kernel is usually done immediately
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)
