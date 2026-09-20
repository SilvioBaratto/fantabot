"""A live loop that can be stopped by a file, not only by a signal.

The half 3.9a carried forward and 3.7 recorded against the watch: **`asta bid` does not poll
the cooperative stop flag.** On POSIX the supervisor's first stop is a `SIGINT` and the
handler `_disarm_on_sigint` installs answers it. On Windows nothing is sent — `stopflag.py`'s
own docstring is the diagnosis, `CTRL_BREAK_EVENT` reaching a Python child as SIGBREAK and
killing it before `except KeyboardInterrupt` runs — so a supervised child there can only be
stopped by the flag, and neither live command ever looked at one. For a watch that is
harmless: the journal flushes per line. For a bidder it is the difference between "stop
bidding" and "keep bidding until the grace timer kills you".

**How the child learns its flag path: it derives it, like everything else in this
repository.** Not an argv token and not an environment variable — `room_stop_path` takes the
journal path and the room, and the app and the CLI call the same function. The alternative
was a `--stop-flag` option, which is a second spelling of one fact and the exact shape of the
`./data/aste_live` footgun: a path that resolves differently depending on who launched the
process.

**The role is in the name because a watch and a bid can run on one room.** Same reasoning as
`stop_path`'s collector/loader split — one file for both would let a stop aimed at the watch
end the bidding.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _paths import module_file

ASTA = "fantabot.interface.asta"


class TestTheFlagPathIsOneDerivation:
    def test_it_hangs_off_the_journal_and_names_the_room_and_the_role(
        self, tmp_path: Path
    ) -> None:
        from fantabot.adapters.files.stopflag import room_stop_path

        flag = room_stop_path(tmp_path / "room_journal.jsonl", "4103937", "bid")

        assert flag.parent == tmp_path
        assert flag.name == "room-4103937.bid.stop"

    def test_a_watch_and_a_bid_on_one_room_do_not_share_it(self, tmp_path: Path) -> None:
        """The whole reason the role is in the name. A stop aimed at the watch would
        otherwise end the bidding, and the operator would read it as the room going quiet."""
        from fantabot.adapters.files.stopflag import room_stop_path

        journal = tmp_path / "room_journal.jsonl"

        assert room_stop_path(journal, "L1", "bid") != room_stop_path(journal, "L1", "watch")

    def test_two_rooms_do_not_share_it_either(self, tmp_path: Path) -> None:
        from fantabot.adapters.files.stopflag import room_stop_path

        journal = tmp_path / "room_journal.jsonl"

        assert room_stop_path(journal, "L1", "bid") != room_stop_path(journal, "L2", "bid")

    def test_an_unknown_role_is_refused_rather_than_filed_somewhere_new(
        self, tmp_path: Path
    ) -> None:
        """`stop_path`'s rule, and for its reason: a typo that silently creates a third
        flag is a run nobody can stop, reported as a run that ignores Stop."""
        from fantabot.adapters.files.stopflag import room_stop_path

        with pytest.raises(ValueError, match=r"watch, bid"):
            room_stop_path(tmp_path / "room_journal.jsonl", "L1", "collect")


class TestARoomWithNoShardIsRefusedByName:
    """`ResolvedRoom.db` is optional because the platform's own field is.

    Every read and every raise is keyed by the shard, so a room without one cannot be driven
    at all. Refused here rather than five calls into the adapter, where it arrives as a
    `TypeError` about `None` — a sentence about Python, not about the room.
    """

    def test_a_shard_builds_a_router(self) -> None:
        from fantabot.application.asta_session import lot_router

        assert lot_router(4, "L1").node == "auction"

    def test_no_shard_is_a_refusal_naming_the_room(self) -> None:
        from fantabot.application.asta_room import RoomRefused
        from fantabot.application.asta_session import lot_router

        with pytest.raises(RoomRefused, match="L1"):
            lot_router(None, "L1")

    def test_shard_zero_is_a_shard(self) -> None:
        """The reason the guard is `is None` and not a truth test: FantaLab's shards are
        0-indexed, and `if not db` would refuse the first one."""
        from fantabot.application.asta_session import lot_router

        assert lot_router(0, "L1") is not None


class TestTheTwoStagesAreHonoured:
    """`stop_poll` is the rule, once, for both live commands. Pure — every effect injected."""

    @staticmethod
    def _poll(stages: list[str | None], armed: list[bool]) -> tuple[list[bool], list[str]]:
        """One poll per stage, in order. Returns what the loop was told and what was said."""
        from fantabot.application.asta_session import stop_poll

        said: list[str] = []
        remaining = list(stages)
        # Built *with* `armed` already in the state the case describes: `stop_poll` snapshots
        # it at composition, which is the property `test_a_sigint_and_the_flag_are_one_
        # request_and_disarm_once` exists for. A helper that mutated `armed` after this line
        # would be testing the old, defective reading.
        keep_going = stop_poll(
            read_stage=lambda: remaining.pop(0), armed=armed, announce=said.append
        )
        return [keep_going(cycle) for cycle, _ in enumerate(stages)], said

    def test_no_request_changes_nothing(self) -> None:
        armed = [True]
        kept, said = self._poll([None, None, None], armed)

        assert kept == [True, True, True] and armed == [True] and said == []

    def test_the_first_stage_disarms_and_keeps_drawing(self) -> None:
        """*Disarm* means stop deciding and keep drawing — the whole reason the gesture has
        two stages. A loop that exited here would take the walk-away off the screen at the
        exact moment the operator has to bid by hand."""
        from fantabot.adapters.files.stopflag import DISARM

        armed = [True]
        kept, said = self._poll([DISARM, None, None], armed)

        assert kept == [True, True, True], "a disarm ended the run"
        assert armed == [False], "a disarm left the writer armed"
        assert said, "a disarm said nothing: on screen it is indistinguishable from a quiet room"

    def test_the_second_stage_leaves(self) -> None:
        from fantabot.adapters.files.stopflag import EXIT

        armed = [True]
        kept, _ = self._poll([EXIT], armed)

        assert kept == [False] and armed == [False]

    def test_a_run_that_was_never_armed_leaves_on_the_first_request(self) -> None:
        """`_disarm_on_sigint`'s own rule, and it has to be this one too or the platforms
        disagree. On POSIX `ProcessJob.stop` sends a SIGINT as well as writing the flag, and
        a never-armed run ends there on the first click; on Windows nothing is sent, so a
        first stage that kept it alive would make §12's second criterion true on one platform
        only. A watch is exactly this case — it is started without `--arm`."""
        from fantabot.adapters.files.stopflag import DISARM, EXIT

        assert self._poll([DISARM], armed=[False])[0] == [False]
        assert self._poll([EXIT], armed=[False])[0] == [False]

    def test_an_armed_run_is_not_ended_by_the_stage_that_only_disarms_it(self) -> None:
        """The other side of the same line: the case above must not swallow the two-stage
        gesture on the one run that has something to disarm."""
        from fantabot.adapters.files.stopflag import DISARM

        assert self._poll([DISARM], armed=[True])[0] == [True]

    def test_a_sigint_and_the_flag_are_one_request_and_disarm_once(self) -> None:
        """**On POSIX both arrive**, and reading `armed` to decide would collapse the gesture.

        `ProcessJob.stop` writes the flag *and* sends a `SIGINT` — `_request_stop` then
        `_signal`, in that order. The handler clears `armed[0]` and keeps the run drawing,
        which is stage one; the next poll then reads the same `disarm` off disk. A gate that
        asked "is it armed *now*" would find `False`, take that for "nothing to disarm", and
        end an armed run on its first Stop — on POSIX only, which is precisely the
        cross-platform divergence §12's second criterion forbids.

        So the question is whether the run was armed **when the loop started**, which is a
        fixed fact, not a mutable list two mechanisms both clear.
        """
        from fantabot.adapters.files.stopflag import DISARM
        from fantabot.application.asta_session import stop_poll

        armed = [True]
        said: list[str] = []
        keep_going = stop_poll(
            read_stage=lambda: DISARM, armed=armed, announce=said.append
        )
        # The SIGINT handler, firing between the loop starting and the next poll.
        armed[0] = False

        assert keep_going(0) is True, (
            "a Ctrl-C and a Stop click are one request: the run disarmed and must keep drawing"
        )
        assert keep_going(1) is True, "and the same flag, re-read, is still one request"

    def test_one_request_is_honoured_once_however_many_polls_read_it(self) -> None:
        """**The flag stays on disk**, so every poll after a `disarm` reads a `disarm` —
        `request_stop` escalates only when the caller asks again. Without the latch the
        second read finds `armed` already false, takes that for "nothing to disarm", and
        ends a run the operator asked to keep watching.

        The line is said once for the same reason it matters elsewhere: at a 2 s poll it
        would scroll the heartbeat away inside a minute, and under a supervisor every line
        is a row in the job log.
        """
        from fantabot.adapters.files.stopflag import DISARM

        kept, said = self._poll([DISARM, DISARM, DISARM], armed=[True])

        assert kept == [True, True, True], "a re-read of one disarm ended the run"
        assert len(said) == 1, said


class TestBothLiveCommandsPoll:
    """Structural, and discovered rather than listed: a third live command is covered the
    day it is written. The behavioural half is `TestAstaBidStopsOnTheFlag` below — this is
    what stops the other command quietly losing it again."""

    @staticmethod
    def _tree() -> ast.Module:
        return ast.parse(module_file(ASTA).read_text(encoding="utf-8"))

    def _loop_calls(self) -> list[ast.Call]:
        return [
            call
            for call in ast.walk(self._tree())
            if isinstance(call, ast.Call)
            and any(keyword.arg == "poll_seconds" for keyword in call.keywords)
        ]

    def test_the_discovery_finds_both(self) -> None:
        assert len(self._loop_calls()) == 2

    def test_every_loop_keeps_going_by_asking_the_flag(self) -> None:
        for call in self._loop_calls():
            [keep_going] = [k.value for k in call.keywords if k.arg == "keep_going"]
            assert (
                isinstance(keep_going, ast.Call)
                and isinstance(keep_going.func, ast.Name)
                and keep_going.func.id == "stop_poll"
            ), (
                f"a live loop runs until `{ast.unparse(keep_going)}`, so a supervised child "
                "on Windows — where no signal is sent — cannot be stopped short of the kill"
            )
