"""The cooperative stop flag: two states, addressed to one run, and never latching.

**Why a file and not a signal.** `CTRL_BREAK_EVENT` — the only stop Windows offers a
process group — reaches a Python child as `SIGBREAK` and terminates it *before*
`except KeyboardInterrupt` runs. Measured on the runner: `exited 3221225786`, which is
`STATUS_CONTROL_C_EXIT`, with the child's own "interrupted" line never printed and the
SIGKILL escalation behind it therefore unreachable. A polled file has no such asymmetry:
the child chooses when to look, so its shutdown path always runs.

**Two states, because the gesture has two stages.** *Disarm* says stop deciding and keep
drawing; *exit* says leave. That is the terminal contract `asta bid` documents for its
two Ctrl-Cs, and the reason the flag is not a boolean.

**Addressed, because a flag is a file and files outlive the run that wrote them.** A run
killed between the write and its own shutdown leaves `exit` on disk; the next run reading
a bare state would quit at startup for a reason that no longer exists, for ever. So the
flag names the pid it is for and a child ignores one that is not.

That is not the pid-file scheme `lock.py` argues against, and the difference is which
question the pid is asked. `lock.py` refuses to answer *"is a collector running?"* from a
recorded pid, because pid 40122 may since have become a browser. Here the pid is an
**addressee**, and the only reader that acts on it is the process whose own
`os.getpid()` matches — a fact it knows for certain and does not have to infer.
"""

from __future__ import annotations

import os
from pathlib import Path

from fantabot.adapters.files.stopflag import (
    DISARM,
    EXIT,
    clear_stop,
    read_stop,
    request_stop,
    stop_path,
)


class TestPath:
    def test_it_sits_beside_the_thing_it_stops(self, tmp_path: Path) -> None:
        landing = tmp_path / "live.jsonl"

        assert stop_path(landing) == tmp_path / "live.jsonl.stop"

    def test_two_landing_zones_never_share_one(self, tmp_path: Path) -> None:
        assert stop_path(tmp_path / "a" / "live.jsonl") != stop_path(tmp_path / "b" / "live.jsonl")


class TestTheTwoStages:
    def test_no_flag_reads_as_nothing_to_do(self, tmp_path: Path) -> None:
        assert read_stop(stop_path(tmp_path / "live.jsonl"), pid=os.getpid()) is None

    def test_the_first_request_disarms(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "live.jsonl")

        assert request_stop(path, pid=4242) == DISARM
        assert read_stop(path, pid=4242) == DISARM

    def test_the_second_request_escalates_to_exit(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "live.jsonl")
        request_stop(path, pid=4242)

        assert request_stop(path, pid=4242) == EXIT
        assert read_stop(path, pid=4242) == EXIT

    def test_a_third_request_stays_at_exit(self, tmp_path: Path) -> None:
        """There is no stage after *exit*, and escalating past it would mean inventing
        one. The caller escalates further by killing the process, which is a different
        mechanism on purpose."""
        path = stop_path(tmp_path / "live.jsonl")
        request_stop(path, pid=4242)
        request_stop(path, pid=4242)

        assert request_stop(path, pid=4242) == EXIT

    def test_it_creates_the_directory_it_writes_into(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "nested" / "deeper" / "live.jsonl")

        assert request_stop(path, pid=1) == DISARM
        assert path.exists()


class TestStaleness:
    def test_a_flag_for_another_run_is_not_mine(self, tmp_path: Path) -> None:
        """The latching failure, stated as a test: a previous run wrote *exit* and died,
        and the next run must not read it as its own."""
        path = stop_path(tmp_path / "live.jsonl")
        request_stop(path, pid=4242)
        request_stop(path, pid=4242)

        assert read_stop(path, pid=9999) is None

    def test_requesting_for_a_new_run_replaces_the_stale_flag(self, tmp_path: Path) -> None:
        """Escalation is per run. A stale *exit* must not make the new run's *first* stop
        an exit — that would latch the old run's second Ctrl-C onto the new one."""
        path = stop_path(tmp_path / "live.jsonl")
        request_stop(path, pid=4242)
        request_stop(path, pid=4242)

        assert request_stop(path, pid=9999) == DISARM
        assert read_stop(path, pid=9999) == DISARM
        assert read_stop(path, pid=4242) is None

    def test_an_unreadable_flag_reads_as_nothing_to_do(self, tmp_path: Path) -> None:
        """A half-written or hand-edited file must not stop a run. The flag is advisory;
        a corrupt one is the *absence* of a request, never the presence of one."""
        path = stop_path(tmp_path / "live.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

        assert read_stop(path, pid=os.getpid()) is None

    def test_a_flag_naming_an_unknown_state_reads_as_nothing_to_do(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "live.jsonl")
        request_stop(path, pid=4242)
        path.write_text('{"pid": 4242, "state": "detonate"}', encoding="utf-8")

        assert read_stop(path, pid=4242) is None


class TestClear:
    def test_clearing_removes_the_request(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "live.jsonl")
        request_stop(path, pid=4242)

        clear_stop(path)

        assert read_stop(path, pid=4242) is None

    def test_clearing_what_is_not_there_is_not_an_error(self, tmp_path: Path) -> None:
        """A run clears its flag on the way out, and the ordinary case is that nobody ever
        wrote one."""
        clear_stop(stop_path(tmp_path / "live.jsonl"))


class TestTheModuleItself:
    def test_it_names_no_signal_api(self) -> None:
        """Spec criterion: the flag is the portable half of the stop, and a `signal`
        import here would quietly make it the platform-dependent half again."""
        from fantabot.adapters.files import stopflag

        source = Path(stopflag.__file__).read_text(encoding="utf-8")

        assert "import signal" not in source
        assert "signal." not in source
