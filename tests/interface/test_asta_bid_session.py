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

from fantabot.config import settings
from fantabot.interface.asta import _lega_rules as REAL_LEGA_RULES

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
    world_read: dict[str, Any] = {}

    def _read_plan_inputs(*_a: Any, **kw: Any) -> Any:
        world_read.update(kw)
        return _Namespace(
            pool=["p1"], value={}, prices={}, teams={}, legality=None, names={}
        )

    monkeypatch.setattr(asta, "read_plan_inputs", _read_plan_inputs)
    # **A band unlike every default in sight**, and that is the point of the number: the
    # first version injected `RosterRules()` and asserted `max_bid(500, RosterRules().size)`,
    # so it recomputed its expectation from the very default it had injected. It could catch
    # a *missing* cap and not a *wrong band* — which is the failure mode that matters, since
    # the band sizes both the plan and every `max_cap` the tracker computes.
    monkeypatch.setattr(
        asta,
        "_lega_rules",
        lambda _lega, fmt, **_k: (RosterRules(size=25), "a test band", fmt),
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
    return {"sent": sent, "journalled": journalled, "built": built, "world": world_read}


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

        # The literal, not `max_bid(500, RosterRules().size)`: recomputing the expectation
        # from the injected default is how the first version came to be unable to see a
        # wrong band at all. 25 slots, 24 reserved beyond this lot, so 476.
        assert self._read_the_guards(monkeypatch)["cap"] == 476
        assert max_bid(500, 25) == 476, "the arithmetic this pins, stated once more"
        assert RosterRules().size != 25, (
            "the fixture's band is the production default again, so this test can no longer "
            "tell a cap sized from the room from one sized from a fallback"
        )

    def test_the_purse_starts_at_the_declared_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._read_the_guards(monkeypatch)["budget"] == 500


class TestTheBandTheOperatorStates:
    """`--size`, end to end through the real command body.

    `_lega_rules` has its own tests and they could not see the Typer body forgetting to
    *pass* the flag: replacing `size=size` with `size=0` at the one call site left all 2,188
    tests green. So these drive the command, and they restore the **real** `_lega_rules` —
    `_wire` replaces it with a fixed 25-man band, which would have made the first assertion
    below pass without the flag doing anything at all.

    No database is opened: `--format` always has a value on this command, so a stated size
    takes the branch that answers from the built-in band without reading a lega. That is the
    app's own path — it sends `--size` and `--format` and deliberately no `--lega`.
    """

    def test_the_flag_reaches_the_band_the_cap_divides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`max_bid(500, 25) == 476`, against the built-in 30's `471`. The number printed on
        the screen is the number the cap is computed from."""
        from fantabot.application import asta_session

        _wire(monkeypatch, frame=_frame())
        from fantabot.interface import asta

        monkeypatch.setattr(asta, "_lega_rules", REAL_LEGA_RULES)
        seen: dict[str, Any] = {}

        def loop(**kw: Any) -> Any:
            from fantabot.adapters.http.fantalab.room import LoopReport

            seen["cap"] = kw["max_cap"]()
            return LoopReport(cycles=0, bids_sent=0, refused={})

        monkeypatch.setattr(asta_session, "run_bid_loop", loop)
        result = _invoke(["--arm", "--budget", "500", "--size", "25"])

        assert result.exit_code == 0, result.output
        assert seen["cap"] == 476, "the stated band did not reach the cap"
        assert "roster band: 25" in result.output
        assert "given with --size" in result.output

    def test_the_built_in_band_is_what_it_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half: a body hard-coding 25 would pass the test above alone. Without
        the flag the same path answers on the built-in 30, and `max_bid(500, 30)` is 471."""
        from fantabot.application import asta_session

        _wire(monkeypatch, frame=_frame())
        from fantabot.interface import asta

        monkeypatch.setattr(asta, "_lega_rules", REAL_LEGA_RULES)
        monkeypatch.setattr(settings, "fantabot_league_id", 0, raising=False)
        seen: dict[str, Any] = {}

        def loop(**kw: Any) -> Any:
            from fantabot.adapters.http.fantalab.room import LoopReport

            seen["cap"] = kw["max_cap"]()
            return LoopReport(cycles=0, bids_sent=0, refused={})

        monkeypatch.setattr(asta_session, "run_bid_loop", loop)
        result = _invoke(["--arm", "--budget", "500"])

        assert seen["cap"] == 471
        assert "given with --size" not in result.output

    def test_an_impossible_size_is_an_exit_code_and_not_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`resize_band` raises `ValueError`; the Typer body turns it into a refusal. The
        alternative is `optimize_roster` raising once per two-second cycle, from inside a
        live loop, for an evening."""
        _wire(monkeypatch, frame=_frame())
        from fantabot.interface import asta

        monkeypatch.setattr(asta, "_lega_rules", REAL_LEGA_RULES)

        result = _invoke(["--arm", "--size", "1"])

        assert result.exit_code != 0
        assert "goalkeepers" in result.output
        assert "Traceback" not in result.output


class TestTheFormatIsDetectedRatherThanAssumed:
    """`--format` defaults to "detect", as it does on `asta optimize`.

    It defaulted to `"mantra"`, which is indistinguishable from an operator typing it — so
    `_lega_rules`' override branch fired on *any* Classic lega, printed *"lega 3584692 is
    classic; planning mantra because --format says so"* about a flag nobody passed, and
    threw away the band the lega had declared.

    **Fixing the default forces the ordering fix**, which is why they are one change: `""`
    cannot be handed to `read_plan_inputs(listone=...)`, and that call came *before* the
    detection. The pool and the corpus were selected with the pre-detection value while the
    band used the post-detection one, so the two could describe different formats in the
    same run.
    """

    def test_the_detected_format_selects_the_pool_and_the_corpus(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ordering, as a value: `listone` is what `read_plan_inputs` was actually
        given, and with a Classic lega and no `--format` it has to be `classic`."""
        from fantabot.application import asta_session
        from fantabot.domain.classic.state import ClassicRosterRules
        from fantabot.interface import asta

        wired = _wire(monkeypatch, frame=_frame())
        monkeypatch.setattr(
            asta,
            "_lega_rules",
            lambda _lega, _fmt, **_k: (ClassicRosterRules(), "a test band", "classic"),
        )
        monkeypatch.setattr(
            asta_session,
            "run_bid_loop",
            lambda **_kw: __import__(
                "fantabot.adapters.http.fantalab.room", fromlist=["LoopReport"]
            ).LoopReport(cycles=0, bids_sent=0, refused={}),
        )
        assert _invoke(["--arm"]).exit_code == 0

        assert wired["world"]["listone"] == "classic", (
            "the pool and the corpus were chosen before the format was detected"
        )

    def test_an_explicit_format_still_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`--format` survives as an override — that is what the warning is for. What
        changed is that the default is no longer indistinguishable from one."""
        from fantabot.application import asta_session

        wired = _wire(monkeypatch, frame=_frame())
        monkeypatch.setattr(
            asta_session,
            "run_bid_loop",
            lambda **_kw: __import__(
                "fantabot.adapters.http.fantalab.room", fromlist=["LoopReport"]
            ).LoopReport(cycles=0, bids_sent=0, refused={}),
        )
        assert _invoke(["--arm", "--format", "classic"]).exit_code == 0

        assert wired["world"]["listone"] == "classic"

    def test_an_unknown_format_is_still_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _wire(monkeypatch, frame=_frame())

        result = _invoke(["--arm", "--format", "mantraa"])

        assert result.exit_code != 0
        assert "mantra" in result.output


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
