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
