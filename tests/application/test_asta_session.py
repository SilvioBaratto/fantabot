"""One poll of a live room, composed in one place — so two surfaces cannot each wire their own.

`RoomTracker` was already in `application/`; its **construction** was not. Twenty arguments
were assembled inside `asta room`'s Typer body, and the app's room route would have had to
assemble them again from the same `ResolvedRoom` and the same `PlanInputs`. That is the exact
shape of the drift `CLAUDE.md` records twice — three commands each growing their own value
model, and `GET /asta/plan` building a plan differing from `asta optimize`'s in ten inputs.

The accept criterion of the task that created this module is what the first class asserts: a
headless caller drives one cycle and gets a journal row, with no Rich anywhere in its stack.
Everything is injected — the ledger, the journal, the clock — so this file opens nothing.

The fixture is `test_asta_room_tracker.py`'s, deliberately: the same four players and the same
two-slot rosa, so a frame that differs between the two files is the composition's doing and
not the world's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from fantabot.adapters.http.fantalab.rest import Seat as RoomSeat
from fantabot.application.asta_room import ResolvedRoom
from fantabot.application.asta_session import AstaSession, session_for
from fantabot.application.plan_inputs import PlanInputs
from fantabot.domain.asta.legality import SchemaLegality, SlotRule
from fantabot.domain.asta.live import AssignmentEvent
from fantabot.domain.asta.roles import MantraPlayer, normalize_roles
from fantabot.domain.asta.state import RosterRules
from fantabot.domain.asta.value import NaiveValueModel

SCHEMI = {
    "por-a": SchemaLegality(
        nome="por-a",
        slots=(
            SlotRule("Por", frozenset({"POR"}), frozenset({"POR"})),
            SlotRule("A", frozenset({"A"}), frozenset({"A"})),
        ),
    )
}
WORLD = PlanInputs(
    pool=[
        MantraPlayer("100", normalize_roles(["POR"])),
        MantraPlayer("200", normalize_roles(["A"])),
        MantraPlayer("300", normalize_roles(["A"])),
        MantraPlayer("400", normalize_roles(["A"])),
    ],
    value=NaiveValueModel(
        signals={"100": 5.0, "200": 15.0, "300": 9.0, "400": 20.0},
        prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
    ),
    prices={"100": 10.0, "200": 40.0, "300": 39.0, "400": 95.0},
    teams={"100": "W", "200": "X", "300": "Y", "400": "Z"},
    names={"100": "Portiere", "200": "Bomber", "300": "Riserva", "400": "Occasione"},
    roles={"100": ["POR"], "200": ["A"], "300": ["A"], "400": ["A"]},
    legality=SCHEMI,
    sentiment=None,
)
BRIDGE = {"uuid-gk": 100, "uuid-a1": 200, "uuid-a2": 300, "uuid-a3": 400}
RULES = RosterRules(size=2, min_goalkeepers=1, min_movement=1)

#: Our uid, and the seat it holds. Two ids, not one: `bid.Seat` carries both because a raise
#: payload has to, and mixing them is a `200` that drives somebody else's team all evening.
OUR_UID = "me"
OUR_TEAM = "us"


def _room(**kw: object) -> ResolvedRoom:
    fields: dict[str, object] = {
        "fantaleague_id": "fl-1",
        "db": 3,
        "seat": RoomSeat(
            fantateam_id=OUR_TEAM, user_id=OUR_UID, position=1, team_name="Noi",
            max_credits=None,
        ),
        "num_teams": 8,
        "num_credits": 100,
        "min_player": 2,
        "max_player": 2,
        "number_of_players_selection": "static",
        "min_goalkeepers": 1,
        "max_goalkeepers": 1,
        "min_others": 1,
        "max_others": 1,
        "players_settings_data": None,
        "asta_type": "mantra",
        "asta_mode": "call",
        "raise_mode": "free",
        # **Not 10 and 20.** `live.DEFAULT_COUNTER_TIME` is 10 and `..._FIRST` is 20, so a
        # fixture using those numbers reads the same whether the room's own values were
        # wired through or dropped on the floor — the first version of this file asserted
        # `seconds_left == 9.0` and stayed green with `counter_time=None` passed in. 7 s is
        # what a real room declares (`CLAUDE.md`: "counter_time is 7-10 s").
        "counter_time": 7,
        "counter_time_first": 15,
        "call_at_quotaz": False,
        "team_names": {OUR_TEAM: "Noi"},
        "admin_id": "the-admin",
        "seat_by_user": {OUR_UID: OUR_TEAM},
    }
    fields.update(kw)
    return ResolvedRoom(**fields)  # type: ignore[arg-type]


def _session(
    rows: list[dict[str, object]] | None = None,
    ledger: tuple[AssignmentEvent, ...] = (),
    room: ResolvedRoom | None = None,
    bridge_refresh: Callable[[], Mapping[str, int]] | None = None,
) -> AstaSession:
    return session_for(
        resolved=room if room is not None else _room(),
        bridge_refresh=bridge_refresh,
        user_id=OUR_UID,
        bridge=BRIDGE,
        world=WORLD,
        rules=RULES,
        budget=100.0,
        lam=0.0,
        ceiling_alpha=1.00,
        bargain_beta=0.00,
        bargain_share=0.10,
        ledger=lambda: list(ledger),
        journal=(lambda row: rows.append(dict(row))) if rows is not None else (lambda _r: None),
    )


def _lot(uuid: str = "uuid-a1", price: int = 5, user_id: str = "rival") -> dict[str, object]:
    return {"player_id": uuid, "price": price, "user_id": user_id, "last_bid_time": 0}


class TestAHeadlessCallerDrivesOneCycle:
    """The task's own acceptance criterion, as a test rather than as prose."""

    def test_and_gets_a_journal_row(self) -> None:
        rows: list[dict[str, object]] = []

        _session(rows).cycle(_lot(), now_ms=1_000)

        assert len(rows) == 1
        assert rows[0]["at_ms"] == 1_000
        assert rows[0]["decision"] == "bid"

    def test_and_the_tuple_the_bid_loop_asks_a_target_of_for(self) -> None:
        """`run_bid_loop` calls `target_of(snapshot)` and expects `(target, walk_away)`. The
        interface used to build that tuple itself, which is the one decision in `target_of`
        that was not paint.

        The target is the **uuid**, not the fantacalcio id: it is compared against the node's
        own `player_id` and travels in the raise payload. Keying a walk-away by fantacalcio id
        and a lot by uuid is defect B1 — "not a target, hold" every two seconds for a whole
        evening, with no bid and no error to notice.
        """
        cycle = _session().cycle(_lot(), now_ms=1_000)

        assert cycle.target == ("uuid-a1", 90)
        assert cycle.frame.walk_away == 90

    def test_and_the_frame_the_screen_draws(self) -> None:
        """One call, both answers. The closure this replaces computed the frame and returned
        two of its fields, so the screen had nothing to draw."""
        cycle = _session().cycle(_lot(), now_ms=1_000)

        assert cycle.frame.lot_name == "Bomber"
        assert cycle.frame.lot_id == "uuid-a1"

    def test_with_no_rich_anywhere_in_its_stack(self) -> None:
        import _importgraph

        assert not _importgraph.reaches("fantabot.application.asta_session", "rich")

    def test_and_no_database_either(self) -> None:
        """Same guard as `asta_room`'s, for the same reason: the collection and decision path
        must not be able to acquire one."""
        import _importgraph

        for target in ("fantabot.adapters.persistence", "fantabot.interface", "typer"):
            assert not _importgraph.reaches("fantabot.application.asta_session", target), target


class TestWhenThereIsNothingToBidOn:
    def test_a_lot_we_do_not_want_is_no_target(self) -> None:
        """`uuid-a2` is the fixture's control: unplanned, and buying him only swaps mu down."""
        cycle = _session().cycle(_lot("uuid-a2", price=50), now_ms=1_000)

        assert cycle.frame.target is None
        assert cycle.target is None

    def test_an_empty_node_is_no_target(self) -> None:
        cycle = _session().cycle(None, now_ms=1_000)

        assert cycle.target is None
        assert cycle.frame.decision == "waiting"


class TestTheWiringTheAppWouldOtherwiseAssembleAgain:
    """Each of these is one keyword the composition could silently drop. None of them raises
    when it is missing — that is what makes them worth a test rather than a type."""

    def test_the_seat_carries_our_uid_and_not_only_our_chair(self) -> None:
        """`already_high` is only reachable through `seat.user_id`. Swap the two ids and the
        room bids against itself all evening at a `200`.

        The target survives the guard, deliberately: he is still who we want, and the refusal
        is re-evaluated by `decide_bid` on the next poll against whatever the node says then.
        """
        cycle = _session().cycle(_lot(user_id=OUR_UID), now_ms=1_000)

        assert cycle.frame.reason == "already_high"
        assert cycle.frame.decision == "pass"

    def test_the_countdown_comes_from_the_rooms_own_counter_time(self) -> None:
        """7 s on a raised lot. A dropped `counter_time` falls back to the domain's default
        and leaves the screen showing a clock the room is not keeping."""
        cycle = _session().cycle(_lot(price=5), now_ms=1_000)

        assert cycle.frame.seconds_left == 6.0

    def test_and_the_longer_one_for_a_lot_nobody_has_bid_on_yet(self) -> None:
        """15 s, not 7: a called player has to be noticed before anyone can bid on him, and
        `counter_time_first` is a second keyword that can be dropped on its own."""
        cycle = _session().cycle(_lot(price=0), now_ms=1_000)

        assert cycle.frame.seconds_left == 14.0

    def test_a_skipped_lot_our_own_raise_stood_on_is_ours(self) -> None:
        """`seat_by_user`. Two of these were ours on 2026-09-01 and missing from the rosa all
        evening — `attribute_passed_lots` rewrites nothing without the map."""
        cycle = _session(
            ledger=(AssignmentEvent("uuid-gk", 0, None, bidder_user_id=OUR_UID),),
        ).cycle(_lot(), now_ms=1_000)

        assert "100" in cycle.frame.owned

    def test_but_the_admins_own_auto_skip_is_not(self) -> None:
        """`admin_id`. 248 of them in one evening; claiming one is claiming a lot nobody bid
        on. The admin is seated here, so only `admin_user_id` can tell the two apart.

        Asserted on `recent` and not on `owned`: a wrongly attributed skip goes to the admin's
        *own* seat, never to ours, so our rosa reads the same either way and the first version
        of this test could not fail. Nor could a `walkaways` assertion — this fixture has one
        keeper and an obligatory keeper slot, so the pool is infeasible the moment he leaves
        it by either route. What actually differs is the sale line the room shows and the
        copilot is briefed on: `Portiere 0 a None` — a lot nobody bought — against
        `Portiere 10 a them`, a purchase invented out of an auto-skip.
        """
        room = _room(seat_by_user={OUR_UID: OUR_TEAM, "the-admin": "them"})
        cycle = _session(
            ledger=(AssignmentEvent("uuid-gk", 0, None, bidder_user_id="the-admin"),),
            room=room,
        ).cycle(_lot(), now_ms=1_000)

        assert cycle.frame.recent == ("Portiere 0 a None",)
        assert "100" not in cycle.frame.owned

    def test_a_lot_the_bridge_cannot_name_triggers_one_refresh(self) -> None:
        """`bridge_refresh`. 41 of 570 pool players were absent from FantaLab's listone on
        2026-08-28, and a signing added mid-evening arrives *as the lot on the block* — the
        most time-critical case there is. Without the keyword the room holds on it all night.
        """
        calls: list[int] = []

        def refresh() -> Mapping[str, int]:
            calls.append(1)
            return {**BRIDGE, "uuid-new": 300}

        cycle = _session(bridge_refresh=refresh).cycle(_lot("uuid-new"), now_ms=1_000)

        assert calls == [1]
        assert cycle.frame.lot_name == "Riserva"


# -- the loop (3.6b) ---------------------------------------------------------------------
#
# 3.6a lifted the cycle; the wiring around it stayed in a 350-line Typer body. That body held
# the `latest` one-slot buffer the budget and cap guards read, the heartbeat filter that
# journals a skipped poll, the error journal, and the `run_bid_loop` call itself — every one
# of them a thing the app's room route would have had to write again from the same parts.
#
# ⚠ The gap 3.6a found and left: **nothing in the suite noticed `asta room` dropping the
# session's answer.** Forcing `target_of`'s return to `None` in the Typer body left 2,079
# tests green — the room would have watched all evening and never bid. `TestTheLoopBidsTheSessionsOwnTarget`
# is that gap closed: the answer and the loop are now joined inside one tested call.

from types import SimpleNamespace  # noqa: E402 — grouped with the tests that need it

from fantabot.application.arming import ARM, AUTO_ACT  # noqa: E402
from fantabot.application.asta_session import STALE_BRIDGE, room_arming  # noqa: E402


class _Writes:
    """A writer that records what reached the room, with an `armed` flag it re-reads per call.

    Not a mock: `run_bid_loop` reads `.sent` off whatever comes back and counts the bid from
    it, so the return value has to be the real shape (`bid_writer`'s own note).
    """

    def __init__(self, armed: list[bool] | None = None) -> None:
        self.sent: list[int] = []
        #: The whole payload, not only its price: the raise carries the pair of ids it is
        #: signed with, and that is the field nothing else in this file can see.
        self.payloads: list[dict[str, object]] = []
        self.armed = [True] if armed is None else armed

    def __call__(self, payload: dict[str, object]) -> object:
        price = int(payload["price"])  # type: ignore[call-overload]
        if not self.armed[0]:
            return SimpleNamespace(sent=False, status=None, price=price)
        self.sent.append(price)
        self.payloads.append(dict(payload))
        return SimpleNamespace(sent=True, status=200, price=price)


def _drive(
    session: AstaSession,
    snapshots: list[dict[str, object] | None],
    *,
    write: object | None = None,
    frames: list[object] | None = None,
    errors: list[tuple[Exception, int]] | None = None,
    fallback_budget: int = 100,
    fallback_cap: int = 100,
    read: Callable[[], object] | None = None,
) -> object:
    """One bounded run. Every effect is injected, so this opens nothing and never sleeps."""
    queue = list(snapshots)

    def default_read() -> object:
        return queue.pop(0) if queue else None

    return session.run(
        node=lambda: "auction",
        read=read or default_read,
        write=write or _Writes(),
        now=lambda: 1_000,
        sleep=lambda _s: None,
        on_frame=(lambda frame: frames.append(frame)) if frames is not None else (lambda _f: None),
        on_error=(
            (lambda exc, n: errors.append((exc, n))) if errors is not None else (lambda _e, _n: None)
        ),
        fallback_budget=fallback_budget,
        fallback_cap=fallback_cap,
        poll_seconds=0.0,
        keep_going=lambda cycle: cycle < len(snapshots),
    )


class TestTheLoopBidsTheSessionsOwnTarget:
    """The gap 3.6a found: the session decided, and the body dropped the answer.

    With the wiring here, the decision and the loop that acts on it are one call — there is
    no seam left for a caller to drop it at, and this test fails the moment `cycle.target`
    stops reaching `run_bid_loop`.
    """

    def test_a_lot_we_want_is_raised_on(self) -> None:
        writes = _Writes()

        report = _drive(_session(), [_lot()], write=writes)

        assert writes.sent == [6], "the session's target never reached the loop"
        assert report.bids_sent == 1

    def test_a_lot_we_do_not_want_is_not(self) -> None:
        """`uuid-a2` is the fixture's control — unplanned, and buying him only swaps mu down."""
        writes = _Writes()

        report = _drive(_session(), [_lot("uuid-a2", price=50)], write=writes)

        assert writes.sent == []
        assert report.bids_sent == 0

    def test_the_raise_is_signed_with_the_pair_the_session_was_composed_with(self) -> None:
        """Two ids, and they are not interchangeable: `fantateam_id` is the chair, `user_id`
        is the account. `session_for` assembles the pair once and the loop signs with that
        same object — the Typer body used to build a second `Seat(...)` out of the same two
        fields for `run_bid_loop`, which is two chances to swap them.

        Swapping them is not an error anywhere: it is a `200` that drives somebody else's
        team all evening. Nothing but the payload can see it, which is why this asserts on
        the payload rather than on the frame.
        """
        writes = _Writes()

        _drive(_session(), [_lot()], write=writes)

        [payload] = writes.payloads
        assert payload["fantateam_id"] == OUR_TEAM
        assert payload["user_id"] == OUR_UID
        assert payload["fantaleague_id"] == "fl-1", "the raise names the room it belongs to"

    def test_the_raise_is_capped_by_the_frames_own_walk_away(self) -> None:
        """90 is what `cycle` priced him at. A lot already above it is held, not chased."""
        writes = _Writes()

        _drive(_session(), [_lot(price=90)], write=writes)

        assert writes.sent == []


class TestTheGuardsReadTheLastFrameAndNotTheStartingCredits:
    """`remaining_budget` was a plain int passed once, and after the first lot won it compared
    every bid against a number that had stopped being true. Both guards read the one-slot
    buffer this loop keeps — the buffer the Typer body used to own."""

    def test_the_budget_guard_reads_the_frame(self) -> None:
        writes = _Writes()

        _drive(_session(), [_lot()], write=writes, fallback_budget=0)

        assert writes.sent == [6], "the loop guarded against the fallback, not the frame"

    def test_and_so_does_the_cap(self) -> None:
        writes = _Writes()

        _drive(_session(), [_lot()], write=writes, fallback_cap=0)

        assert writes.sent == [6], "the loop capped at the fallback, not the frame"


class TestThePollsThatMeanTheLoopIsInTrouble:
    """`waiting_row` and `error_row` are the two rows a screen cannot show and a journal must."""

    def test_a_poll_with_no_lot_journals_a_waiting_row(self) -> None:
        """`run_bid_loop` short-circuits before `cycle` on an empty node, so nothing inside
        `RoomTracker` ever sees this poll. Without the row the gap reads as a stall."""
        rows: list[dict[str, object]] = []

        _drive(_session(rows), [None])

        assert rows == [{"at_ms": 1_000, "decision": "waiting"}]

    def test_a_failed_poll_journals_an_error_row_and_reaches_the_screen(self) -> None:
        rows: list[dict[str, object]] = []
        seen: list[tuple[Exception, int]] = []

        def boom() -> object:
            raise TimeoutError("hotel wifi")

        _drive(_session(rows), [_lot()], read=boom, errors=seen)

        assert rows == [{"at_ms": 1_000, "decision": "error", "error": "TimeoutError"}]
        assert [type(exc).__name__ for exc, _n in seen] == ["TimeoutError"]
        assert seen[0][1] == 1, "the consecutive count the banner reports"

    def test_a_bid_poll_journals_once_and_not_twice(self) -> None:
        """`cycle` journals its own row. A heartbeat that journaled every line would double
        every poll that got as far as a decision."""
        rows: list[dict[str, object]] = []

        _drive(_session(rows), [_lot()])

        assert len(rows) == 1
        assert rows[0]["decision"] == "bid"


class TestTheScreenSeesEveryFrame:
    def test_the_painter_is_handed_the_frame_each_poll(self) -> None:
        frames: list[object] = []

        _drive(_session(), [_lot(), _lot("uuid-a2", price=50)], frames=frames)

        assert [f.lot_id for f in frames] == ["uuid-a1", "uuid-a2"]  # type: ignore[attr-defined]

    def test_a_poll_with_no_lot_paints_nothing_new(self) -> None:
        """The screen holds the last good picture; `cycle` never ran, so there is no frame."""
        frames: list[object] = []

        _drive(_session(), [None], frames=frames)

        assert frames == []


class TestTheWriterIsConsultedPerBidAndNotCapturedAtLoopStart:
    """The disarm property, at this seam. `armed` is a list so a Ctrl-C can clear it without
    a global, and the loop must ask again on the very next write — a writer whose answer was
    captured once keeps bidding after the operator disarmed."""

    def test_a_disarm_between_polls_holds_the_next_raise(self) -> None:
        armed = [True]
        writes = _Writes(armed)

        def read() -> object:
            if writes.sent:  # the operator's Ctrl-C, delivered between the two polls
                armed[0] = False
            return _lot()

        report = _drive(_session(), [_lot(), _lot()], write=writes, read=read)

        assert writes.sent == [6], "a raise was sent after the operator disarmed"
        assert report.cycles == 2, "disarming must not end the run — it keeps watching"


class TestTheStaleBridgeRefusesArmingAndNotTheRun:
    """A bridge the refresh could not renew is a reason to refuse *arming*, never the run:
    the room still draws and can be watched, exactly as a disarmed run always could.

    4 hours is strictly tighter than the 5-hour-stale copy that missed 16 transfer-deadline
    signings on 2026-08-28 — the incident this guards against.
    """

    def test_both_locks_open_and_a_fresh_bridge_arms(self) -> None:
        decision = room_arming(
            arm=True, auto_act=True, bridge_age=30.0, max_bridge_age_hours=4.0
        )

        assert decision.armed is True
        assert decision.closed == ()

    def test_a_stale_bridge_is_a_third_lock_named_on_its_own(self) -> None:
        decision = room_arming(
            arm=True, auto_act=True, bridge_age=5 * 3600.0, max_bridge_age_hours=4.0
        )

        assert decision.armed is False
        assert decision.closed == (STALE_BRIDGE,)

    def test_an_unknown_age_is_not_stale(self) -> None:
        """A pre-envelope cache has no age to compare, and refusing to arm on information a
        room never had the chance to write would punish the upgrade itself."""
        decision = room_arming(
            arm=True, auto_act=True, bridge_age=None, max_bridge_age_hours=4.0
        )

        assert decision.armed is True

    def test_every_shut_lock_is_named_together(self) -> None:
        """Both, not the first: an operator with three shut fixes one, retries, and is told
        about the next. `Arming.closed` lists them all."""
        decision = room_arming(
            arm=False, auto_act=False, bridge_age=5 * 3600.0, max_bridge_age_hours=4.0
        )

        assert decision.closed == (AUTO_ACT, ARM, STALE_BRIDGE)

    def test_a_stale_bridge_never_refuses_the_run_itself(self) -> None:
        """There is no third return value and no exception — the only thing it can say is
        `armed is False`."""
        decision = room_arming(
            arm=True, auto_act=True, bridge_age=99 * 3600.0, max_bridge_age_hours=4.0
        )

        assert isinstance(decision.armed, bool)
