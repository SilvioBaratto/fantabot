"""`asta bid` drives the same composition `asta room` does, and its answer reaches the writer.

3.9a's open half. The command that spends credits carried its own copy of the fold: a
`RoomTracker` assembled from twenty keywords in the Typer body, a `latest` one-slot buffer,
a `target_of` closure, the heartbeat that journals a skipped poll and the error row — all of
it duplicated from `asta room`'s body before 3.6 lifted that one. `CLAUDE.md` records twice
over where a second copy leads, and both times the copy that fell behind was the one that
spends money: three commands each grew their own value model, and `GET /asta/plan` built a
plan differing from `asta optimize`'s in ten inputs.

**The gap 3.6a measured applies here unchanged.** Forcing `asta room`'s Typer body to drop the
session's answer left all 2,079 tests green — it would have watched all evening and never bid
— and `asta bid` has exactly the same seam today: its `target_of` is a closure nothing drives
end to end, because the one harness that runs the command fakes `run_bid_loop` whole. That is
what `test_the_session_answer_reaches_the_writer` is for, and it is the mutation to run when
this file is touched.

What deliberately stays in `interface/`: the paint (the heartbeat line, the one-shot note, the
error line), the poll clock (`cycle_ms` is measured around the journal, never inside
`application/` — the same rule as `_today`), and `bid_writer`, the gate the two locks meet at.
"""

from __future__ import annotations

import ast
import contextlib
from collections.abc import Mapping
from typing import Any

import pytest
from _paths import module_file
from typer.testing import CliRunner

ASTA = "fantabot.interface.asta"


def _frame(**over: Any) -> Any:
    """A `RoomFrame` with one lot on the block and a target we would pay 31 for."""
    from fantabot.application.asta_room import RoomFrame

    fields: dict[str, Any] = {
        "lot_id": "uuid-1", "lot_name": "Yildiz", "price": 12, "high_bidder": "them",
        "seconds_left": 8.0, "node": "auction", "target": "uuid-1", "walk_away": 31,
        "provenance": "ceiling", "decision": "bid", "reason": None, "note": "a note",
        "credits_left": 420, "max_cap": 99, "owned": (), "plan": ("uuid-1",),
        "unresolved_sales": 0, "walkaways": {"1": 31.0}, "schemi_open": 11, "recent": (),
    }
    fields.update(over)
    return RoomFrame(**fields)


def _wire(monkeypatch: pytest.MonkeyPatch, *, frame: Any) -> dict[str, Any]:
    """Everything between `asta bid`'s entry and the lifted loop, faked.

    No socket and no database. What is real is the command body and `AstaSession`: the
    tracker is a stand-in so the frame under test is the one this file wrote, and the loop is
    scripted so the assertion can be about what crossed, not about how many polls happened.
    """
    from fantabot import config
    from fantabot.adapters.files import room_journal
    from fantabot.adapters.http.fantalab import listone, room
    from fantabot.adapters.http.fantalab.rtdb import BidOutcome
    from fantabot.adapters.persistence import database_manager, news_sentiment
    from fantabot.application import asta_session
    from fantabot.domain.asta.state import RosterRules
    from fantabot.interface import asta

    monkeypatch.setattr(config, "_DOTENV_INJECTED", {})
    monkeypatch.setenv(config.AUTO_ACT_VAR, "true")
    monkeypatch.setattr(listone, "fetch", lambda **_k: {"uuid-1": 1})
    monkeypatch.setattr(listone, "cache_age", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(listone, "is_stale", lambda *_a, **_k: False)
    monkeypatch.setattr(
        database_manager, "get_session", lambda: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(news_sentiment, "NewsSentimentSource", lambda _s: None)
    monkeypatch.setattr(asta, "sentiment_rows", lambda *_a, **_k: [])
    monkeypatch.setattr(
        asta,
        "read_plan_inputs",
        lambda *_a, **_k: _Namespace(
            pool=["p1"], value={}, prices={}, teams={}, legality=None, names={}
        ),
    )
    monkeypatch.setattr(
        asta, "_lega_rules", lambda _lega, fmt, **_k: (RosterRules(), "a test band", fmt)
    )

    journalled: list[Mapping[str, Any]] = []
    monkeypatch.setattr(
        room_journal,
        "RoomJournal",
        lambda _p: _Namespace(write=journalled.append, close=lambda: None),
    )

    built: dict[str, Any] = {}

    def _tracker(**kw: Any) -> Any:
        built.update(kw)
        return _Namespace(cycle=lambda _snap, **_k: frame)

    monkeypatch.setattr(asta_session, "RoomTracker", _tracker)

    sent: list[dict[str, Any]] = []

    class _Router:
        node = "auction"

        def __init__(self, **_k: Any) -> None:
            pass

        def read_lot(self) -> tuple[Any, str]:
            return ({"lot": "uuid-1"}, "auction")

        def write_raise(self, payload: dict[str, Any]) -> Any:
            sent.append(dict(payload))
            return BidOutcome(
                price=payload["price"], node="auction", dry_run=False, sent=True, status=200
            )

    # Patched at the factory, not the class. Since 3.11 the router is built in
    # `application/asta_session.lot_router` — binding `place_raise` to a shard is a
    # write, and the T-spine rule kept it on the ratchet until it moved out of the
    # Typer body. A patch on the adapter class is inert from here.
    monkeypatch.setattr(asta_session, "lot_router", lambda _db, _league: _Router())

    # The safety net, and it is load-bearing: a Typer body that still calls the adapter's
    # loop directly runs the *real* one, whose `keep_going` is `lambda _cycle: True` — so a
    # red assertion would have been a hung suite instead. Every test below patches the loop
    # where the lifted composition looks it up; reaching it any other way fails here, by name.
    def _direct(**_kw: Any) -> Any:
        raise AssertionError(
            "the Typer body drove `room.run_bid_loop` itself: the lift did not happen"
        )

    monkeypatch.setattr(room, "run_bid_loop", _direct)
    return {"sent": sent, "journalled": journalled, "built": built}


class _Namespace:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def _invoke(extra: list[str] | None = None) -> Any:
    return CliRunner().invoke(
        __import__("fantabot.interface.app", fromlist=["app"]).app,
        ["asta", "bid", "--league", "L1", "--db", "1", "--team", "T1", "--user", "U1",
         *(extra or ["--arm"])],
    )


class TestTheAnswerCrossesTheSeam:
    def test_the_session_answer_reaches_the_writer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mutation this file exists for: drop the decision and nothing else notices.

        `asta room`'s identical seam left 2,079 tests green when its `target_of` was forced
        to `None`. Here the claim is a number — the frame's own `walk_away`, 31 — so a body
        that forwards nothing sends nothing and this goes red.
        """
        from fantabot.application import asta_session

        wired = _wire(monkeypatch, frame=_frame())
        seen: dict[str, Any] = {}

        def loop(**kw: Any) -> Any:
            from fantabot.adapters.http.fantalab.room import LoopReport

            seen["target"] = kw["target_of"]({"lot": "uuid-1"})
            if seen["target"] is not None:
                kw["write"]({"price": seen["target"][1]})
            return LoopReport(cycles=1, bids_sent=1, refused={})

        monkeypatch.setattr(asta_session, "run_bid_loop", loop)
        result = _invoke()

        assert result.exit_code == 0, result.output
        assert seen["target"] == ("uuid-1", 31), (
            "asta bid's loop was handed no target: the decision does not reach the writer"
        )
        assert [p["price"] for p in wired["sent"]] == [31]

    def test_the_seat_signs_the_raise_and_the_tracker_decides_with_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One `Seat`, bound once. Two constructions out of the same two fields is two
        chances to swap them — a `200` that drives somebody else's team all evening, and
        the seventh mutation of 3.6b, which no assertion on a frame could see."""
        from fantabot.application import asta_session

        wired = _wire(monkeypatch, frame=_frame())
        seen: dict[str, Any] = {}

        def loop(**kw: Any) -> Any:
            from fantabot.adapters.http.fantalab.room import LoopReport

            seen["seat"] = kw["seat"]
            seen["fantaleague_id"] = kw["fantaleague_id"]
            return LoopReport(cycles=0, bids_sent=0, refused={})

        monkeypatch.setattr(asta_session, "run_bid_loop", loop)
        assert _invoke().exit_code == 0

        assert seen["seat"].fantateam_id == "T1" and seen["seat"].user_id == "U1"
        assert seen["fantaleague_id"] == "L1"
        assert wired["built"]["seat"] is seen["seat"], (
            "the tracker decides with one seat and the raise is signed with another"
        )

    def test_the_unauthenticated_room_facts_are_stated_not_defaulted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`asta bid` never fetches `RoomConfig`, so it has no admin uid, no chair table and
        no countdown — and every one of those degrades silently when it is missing. They are
        passed as `None` out loud, which is why the composition takes them as required
        keywords rather than defaulting them here as well as in `RoomTracker`."""
        from fantabot.application import asta_session

        wired = _wire(monkeypatch, frame=_frame())
        monkeypatch.setattr(
            asta_session,
            "run_bid_loop",
            lambda **_kw: __import__(
                "fantabot.adapters.http.fantalab.room", fromlist=["LoopReport"]
            ).LoopReport(cycles=0, bids_sent=0, refused={}),
        )
        assert _invoke().exit_code == 0

        built = wired["built"]
        assert built["admin_user_id"] is None and built["seat_by_user"] is None
        assert built["counter_time"] is None and built["counter_time_first"] is None
        assert built["bridge_refresh"] is not None, (
            "the listone endpoint is unauthenticated: asta bid can and does re-resolve"
        )


class TestTheGuardsBeforeTheFirstFrame:
    """The two numbers the loop reads *before* a poll has ever landed.

    Both were survivors of this slice's own mutation battery: raising either to a billion
    left 2,108 tests green. They are not incidental — `max_cap` is the last line of defence
    ("`docs/fantalab/01:142` calls it client-enforced, and `06:389-412` shows the RTDB rules
    validating only that a raise exceeds the current price"), and the budget guard is the one
    thing between a plan and an overdraft. From the first frame on both read the frame; the
    window they cover is the one where nothing else can.

    The hole predates the lift — `_cap()` and `_remaining()` had the same fallbacks and the
    same absence of a test — which is why it is recorded here rather than in a defect note.
    """

    def _read_the_guards(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
        from fantabot.application import asta_session

        _wire(monkeypatch, frame=_frame())
        seen: dict[str, int] = {}

        def loop(**kw: Any) -> Any:
            from fantabot.adapters.http.fantalab.room import LoopReport

            # Read before anything is polled: this is the pre-first-frame window.
            seen["cap"] = kw["max_cap"]()
            seen["budget"] = kw["remaining_budget"]()
            return LoopReport(cycles=0, bids_sent=0, refused={})

        monkeypatch.setattr(asta_session, "run_bid_loop", loop)
        assert _invoke(["--arm", "--budget", "500"]).exit_code == 0
        return seen

    def test_the_cap_reserves_a_credit_for_every_slot_still_owed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`max_bid(500, 30)` — 29 slots reserved beyond this one, so 471 and not 500.

        A cap that is simply the purse is no cap: `reservations` returns the whole remaining
        budget for a target whose removal makes the roster infeasible, which reads as "pay
        anything" with 28 slots still empty.
        """
        from fantabot.domain.asta.bid import max_bid
        from fantabot.domain.asta.state import RosterRules

        assert self._read_the_guards(monkeypatch)["cap"] == max_bid(500, RosterRules().size)
        assert max_bid(500, RosterRules().size) < 500, (
            "the fixture's band reserves nothing, so this test could not tell a cap from a purse"
        )

    def test_the_purse_starts_at_the_declared_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._read_the_guards(monkeypatch)["budget"] == 500


class TestThePaintStaysInTheInterface:
    def test_the_heartbeat_is_still_printed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The room paints a Rich `Live`; `asta bid` prints lines, and the heartbeat is all
        the operator is reading. A lift that journaled it and stopped printing it would
        leave an armed run with a blank terminal."""
        from fantabot.application import asta_session

        _wire(monkeypatch, frame=_frame())

        def loop(**kw: Any) -> Any:
            from fantabot.adapters.http.fantalab.room import LoopReport

            kw["heartbeat"]("[1] waiting for a lot")
            return LoopReport(cycles=1, bids_sent=0, refused={})

        monkeypatch.setattr(asta_session, "run_bid_loop", loop)
        result = _invoke()

        assert "waiting for a lot" in result.output

    def test_a_waiting_poll_is_journaled_with_its_cycle_ms(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`run_bid_loop` short-circuits before `cycle` on a poll with no lot, so nothing
        inside the tracker sees it. The row has to come from the loop, and it has to come
        through the timed sink or the one row that means "the loop is in trouble" is the one
        row with no timing."""
        from fantabot.application import asta_session

        wired = _wire(monkeypatch, frame=_frame())

        def loop(**kw: Any) -> Any:
            from fantabot.adapters.http.fantalab.room import LoopReport

            kw["heartbeat"]("[1] waiting for a lot")
            return LoopReport(cycles=1, bids_sent=0, refused={})

        monkeypatch.setattr(asta_session, "run_bid_loop", loop)
        assert _invoke().exit_code == 0

        waiting = [row for row in wired["journalled"] if row.get("decision") == "waiting"]
        assert len(waiting) == 1 and "cycle_ms" in waiting[0]

    def test_the_note_is_said_once_not_once_a_poll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """At a 2 s cycle the same line scrolls the heartbeat away inside a minute, and the
        heartbeat is all there is to read."""
        from fantabot.application import asta_session

        _wire(monkeypatch, frame=_frame(note="unresolved lot"))

        def loop(**kw: Any) -> Any:
            from fantabot.adapters.http.fantalab.room import LoopReport

            for _ in range(4):
                kw["target_of"]({"lot": "uuid-1"})
            return LoopReport(cycles=4, bids_sent=0, refused={})

        monkeypatch.setattr(asta_session, "run_bid_loop", loop)
        result = _invoke()

        assert result.output.count("unresolved lot") == 1


class TestThereIsOneComposition:
    """Structural, and the half that keeps the seam closed once it is closed.

    The behavioural tests above would still pass over a body that rebuilt its own tracker
    and happened to agree with the session today. `CLAUDE.md`'s note is that a second copy
    is not a second opinion, it is a slow divergence nobody is told about.
    """

    @staticmethod
    def _tree() -> ast.Module:
        return ast.parse(module_file(ASTA).read_text(encoding="utf-8"))

    def _named(self, name: str) -> list[ast.Call]:
        return [
            node
            for node in ast.walk(self._tree())
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == name)
                or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
            )
        ]

    def test_the_interface_assembles_no_tracker(self) -> None:
        assert self._named("RoomTracker") == [], (
            "a second twenty-keyword construction: the one `application/asta_session.py` exists"
            " to prevent"
        )

    def test_the_interface_drives_no_loop_of_its_own(self) -> None:
        assert self._named("run_bid_loop") == [], (
            "the Typer body calls the loop directly, so the decision and the act are two "
            "calls again and the answer can be dropped between them"
        )

    def test_both_live_commands_compose_through_the_same_module(self) -> None:
        """Discovered, not listed: whatever factory each command uses, it must come from
        `application/asta_session.py` — the room's takes a `ResolvedRoom`, the bidder's
        cannot, and that difference is the whole reason there are two entry points."""
        factories = {
            call.func.id
            for call in ast.walk(self._tree())
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in {"session_for", "session_from"}
        }
        assert factories == {"session_for", "session_from"}, (
            f"the two live commands compose through {sorted(factories)}"
        )
