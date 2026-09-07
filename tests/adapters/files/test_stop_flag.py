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

**Addressed by role, because a flag is a file and files outlive the run that wrote them.**
A run killed between the write and its own shutdown leaves `exit` on disk; the next run
reading it would quit at startup for a reason that expired.

The first version addressed the flag to the child's pid. It did not work on Windows — the
supervisor wrote `disarm` for pid 2300 and the child polling that file never matched
(`app-ci` run 34112470790) — and it was duplicating a guarantee that already existed:
`lock.py` allows one holder per (landing zone, role), which is exactly the identity the
pid stood in for. So the flag is `<landing>.<role>.stop`, the role is in the name because
a collector and a loader share a landing zone by design, and **staleness is handled by
the holder clearing the flag as it starts**.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fantabot.adapters.files.lock import COLLECTOR, LOADER
from fantabot.adapters.files.stopflag import (
    DISARM,
    EXIT,
    clear_stop,
    read_stop,
    request_stop,
    stop_path,
    wait_for_stop,
)


class TestPath:
    def test_it_sits_beside_the_thing_it_stops_and_names_the_role(self, tmp_path: Path) -> None:
        landing = tmp_path / "live.jsonl"

        assert stop_path(landing, COLLECTOR) == tmp_path / "live.jsonl.collector.stop"

    def test_two_landing_zones_never_share_one(self, tmp_path: Path) -> None:
        assert stop_path(tmp_path / "a" / "live.jsonl", LOADER) != stop_path(
            tmp_path / "b" / "live.jsonl", LOADER
        )

    def test_the_two_roles_on_one_landing_zone_never_share_one(self, tmp_path: Path) -> None:
        """Collector-plus-loader is the *intended* pairing — `lock.py` exists because a
        single mutex would ban the normal case. One flag for both would do the same thing
        to the stop: an operator ending the loader would end the collector with it."""
        landing = tmp_path / "live.jsonl"

        assert stop_path(landing, COLLECTOR) != stop_path(landing, LOADER)

    def test_an_unknown_role_is_refused(self, tmp_path: Path) -> None:
        """Same guard as `lock_path`, and for the same reason: a typo would silently open
        a third flag file that nobody polls."""
        import pytest

        with pytest.raises(ValueError, match="is not a role"):
            stop_path(tmp_path / "live.jsonl", "watcher")


class TestTheTwoStages:
    def test_no_flag_reads_as_nothing_to_do(self, tmp_path: Path) -> None:
        assert read_stop(stop_path(tmp_path / "live.jsonl", COLLECTOR)) is None

    def test_the_first_request_disarms(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)

        assert request_stop(path) == DISARM
        assert read_stop(path) == DISARM

    def test_the_second_request_escalates_to_exit(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        request_stop(path)

        assert request_stop(path) == EXIT
        assert read_stop(path) == EXIT

    def test_a_third_request_stays_at_exit(self, tmp_path: Path) -> None:
        """There is no stage after *exit*, and escalating past it would mean inventing
        one. The caller escalates further by killing the process, which is a different
        mechanism on purpose."""
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        request_stop(path)
        request_stop(path)

        assert request_stop(path) == EXIT

    def test_it_creates_the_directory_it_writes_into(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "nested" / "deeper" / "live.jsonl", COLLECTOR)

        assert request_stop(path) == DISARM
        assert path.exists()


class TestStaleness:
    def test_a_run_that_clears_on_start_does_not_inherit_a_dead_run_s_exit(
        self, tmp_path: Path
    ) -> None:
        """The latching failure, stated as a test. A previous run reached *exit* and died
        without cleaning up; the next one must not read that as its own and quit at
        startup for ever.

        Clearing is what the *holder* does, and it is safe precisely because it holds the
        role lock — nothing else can be waiting on what it erases.
        """
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        request_stop(path)
        request_stop(path)
        assert read_stop(path) == EXIT

        clear_stop(path)  # what a starting run does

        assert read_stop(path) is None

    def test_the_new_run_s_first_request_is_a_disarm_again(self, tmp_path: Path) -> None:
        """Escalation must not carry over either: a dead run's second click landing as
        this run's first would skip the stage the operator has not asked for yet."""
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        request_stop(path)
        request_stop(path)
        clear_stop(path)

        assert request_stop(path) == DISARM

    def test_an_unreadable_flag_reads_as_nothing_to_do(self, tmp_path: Path) -> None:
        """A half-written or hand-edited file must not stop a run. The flag is advisory;
        a corrupt one is the *absence* of a request, never the presence of one."""
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

        assert read_stop(path) is None

    def test_a_flag_naming_an_unknown_state_reads_as_nothing_to_do(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        request_stop(path)
        path.write_text('{"pid": 4242, "state": "detonate"}', encoding="utf-8")

        assert read_stop(path) is None


class TestClear:
    def test_clearing_removes_the_request(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        request_stop(path)

        clear_stop(path)

        assert read_stop(path) is None

    def test_clearing_what_is_not_there_is_not_an_error(self, tmp_path: Path) -> None:
        """A run clears its flag on the way out, and the ordinary case is that nobody ever
        wrote one."""
        clear_stop(stop_path(tmp_path / "live.jsonl", COLLECTOR))


class TestTheModuleItself:
    def test_it_names_no_signal_api(self) -> None:
        """Spec criterion: the flag is the portable half of the stop, and a `signal`
        import here would quietly make it the platform-dependent half again."""
        from fantabot.adapters.files import stopflag

        source = Path(stopflag.__file__).read_text(encoding="utf-8")

        assert "import signal" not in source
        assert "signal." not in source


class TestWaiting:
    """`wait_for_stop` is the polling half, and the sleep is injected for the usual
    reason: a test that waited a real cadence would either be slow or be a race."""

    def test_it_returns_the_stage_that_was_asked_for(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        request_stop(path)
        ticks = 0

        async def sleep(_seconds: float) -> None:
            nonlocal ticks
            ticks += 1

        assert asyncio.run(wait_for_stop(path, sleep=sleep)) == DISARM

    def test_it_keeps_looking_until_the_flag_appears(self, tmp_path: Path) -> None:
        """The flag is written by another process *while* this one is waiting, which is
        the only sequence that ever happens in production."""
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        ticks = 0

        async def sleep(_seconds: float) -> None:
            nonlocal ticks
            ticks += 1
            if ticks == 3:
                request_stop(path)

        assert asyncio.run(wait_for_stop(path, sleep=sleep)) == DISARM
        assert ticks == 3

    def test_a_cleared_flag_is_waited_through(self, tmp_path: Path) -> None:
        """The stale-flag case from the waiter's side. A run starts by clearing, so what
        it then waits on is only what was written for it — the leftover it erased cannot
        stop it at its first poll."""
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        request_stop(path)
        request_stop(path)
        clear_stop(path)
        ticks = 0

        async def sleep(_seconds: float) -> None:
            nonlocal ticks
            ticks += 1
            if ticks == 4:
                request_stop(path)

        assert asyncio.run(wait_for_stop(path, sleep=sleep)) == DISARM
        assert ticks == 4

    def test_it_reports_exit_when_that_is_the_stage(self, tmp_path: Path) -> None:
        path = stop_path(tmp_path / "live.jsonl", COLLECTOR)
        request_stop(path)
        request_stop(path)

        async def sleep(_seconds: float) -> None:
            raise AssertionError("the flag was already set; nothing to wait for")

        assert asyncio.run(wait_for_stop(path, sleep=sleep)) == EXIT
