"""One cycle of the live room, as a frozen value.

The closure inside `asta bid` computed every fact the four panes need and then threw all but
one away — it returned `(target, walk_away)` and nothing else, so the screen had nothing to
draw. `RoomTracker.cycle` returns the whole frame instead, and `asta bid` consumes the same
one: a second copy of this logic is the drift `CLAUDE.md` already records once.

Everything is injected — the ledger, the journal, the clock — so this file opens nothing.

**It takes primitives, not `PlanInputs`.** That type lives beside `read_plan_inputs`, whose
body imports `AsteRepository`, and `tests/_importgraph` counts function-body imports: taking
it would give this module a path to Postgres and make its one structural guarantee unprovable.
"""

from __future__ import annotations

from fantabot.application.asta_room import RoomTracker
from fantabot.domain.asta.bid import Seat
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
POOL = [
    MantraPlayer("100", normalize_roles(["POR"])),
    MantraPlayer("200", normalize_roles(["A"])),
    MantraPlayer("300", normalize_roles(["A"])),
]
TEAMS = {"100": "W", "200": "X", "300": "Y"}
PRICES = {"100": 10.0, "200": 40.0, "300": 39.0}
NAMES = {"100": "Portiere", "200": "Bomber", "300": "Riserva"}
VALUE = NaiveValueModel(
    signals={"100": 5.0, "200": 10.0, "300": 9.0},
    prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
)
BRIDGE = {"uuid-gk": 100, "uuid-a1": 200, "uuid-a2": 300}
RULES = RosterRules(size=2, min_goalkeepers=1, min_movement=1)
SEAT = Seat(fantateam_id="us", user_id="me")


def _tracker(ledger=(), journal=None, budget_override=None, **kw):  # type: ignore[no-untyped-def]
    return RoomTracker(
        seat=SEAT,
        bridge=BRIDGE,
        pool=POOL, value=VALUE, prices=PRICES, teams=TEAMS, legality=SCHEMI, names=NAMES,
        rules=RULES,
        budget=100.0 if budget_override is None else budget_override,
        lam=0.0,
        floor=None,
        ledger=lambda: list(ledger),
        journal=journal or (lambda _row: None),
        counter_time=10, counter_time_first=20,
        **kw,
    )


def _lot(uuid: str = "uuid-a1", price: int = 5) -> dict[str, object]:
    return {"player_id": uuid, "price": price, "user_id": "rival", "last_bid_time": 0}


class TestTheFrameCarriesWhatTheScreenNeeds:
    def test_the_lot_is_named_not_left_as_a_uuid(self) -> None:
        """A uuid is unreadable at speed, and the screen is read at speed or not at all."""
        frame = _tracker().cycle(_lot(), now_ms=1_000)

        assert frame.lot_name == "Bomber"
        assert frame.lot_id == "uuid-a1"

    def test_it_carries_the_price_the_bidder_and_the_countdown(self) -> None:
        frame = _tracker().cycle(_lot(price=7), now_ms=1_000)

        assert frame.price == 7
        assert frame.high_bidder == "rival"
        assert frame.seconds_left == 9.0

    def test_the_walkaway_carries_its_provenance(self) -> None:
        """`walk-away 77 (floor a.price)` — never a fused number nobody can argue with."""
        frame = _tracker().cycle(_lot(), now_ms=1_000)

        assert frame.walk_away is not None
        assert frame.provenance in {"marginal", "floor"}

    def test_a_refusal_names_the_guard_that_bound(self) -> None:
        frame = _tracker(ledger=()).cycle({**_lot(price=99), "user_id": "me"}, now_ms=1_000)

        assert frame.decision == "pass"
        assert frame.reason == "already_high"

    def test_the_budget_and_the_cap_come_from_the_same_fold(self) -> None:
        frame = _tracker(ledger=[AssignmentEvent("uuid-gk", 30, "us")]).cycle(
            _lot(), now_ms=1_000
        )

        assert frame.credits_left == 70
        assert frame.max_cap <= frame.credits_left


class TestTheQuietFailures:
    def test_a_lot_outside_the_listone_is_named_as_such(self) -> None:
        """Otherwise it reads exactly like a lot we chose not to chase."""
        frame = _tracker().cycle(_lot(uuid="stranger"), now_ms=1_000)

        assert frame.decision == "hold"
        assert "listone" in (frame.note or "")

    def test_a_sale_the_bridge_cannot_map_is_counted(self) -> None:
        frame = _tracker(ledger=[AssignmentEvent("ghost", 10, "rival")]).cycle(
            _lot(), now_ms=1_000
        )

        assert frame.unresolved_sales == 1

    def test_an_unvaluable_owned_player_does_not_stop_the_evening(self) -> None:
        """The ledger is re-read every cycle and a purchase is never withdrawn, so catching
        the exception without dropping its cause would hold for the rest of the night."""
        frame = _tracker(ledger=[AssignmentEvent("uuid-a1", 10, "us")]).cycle(
            _lot(uuid="uuid-a2"), now_ms=1_000
        )

        assert frame is not None

    def test_no_lot_on_the_block_is_a_frame_not_a_none(self) -> None:
        """The screen still has to draw between lots."""
        frame = _tracker().cycle(None, now_ms=1_000)

        assert frame.lot_id is None
        assert frame.decision == "waiting"


class TestTheJournal:
    def test_every_cycle_is_recorded(self) -> None:
        rows: list[dict] = []
        _tracker(journal=rows.append).cycle(_lot(), now_ms=1_000)

        assert len(rows) == 1
        assert rows[0]["lot"] == "uuid-a1"
        assert "walk_away" in rows[0]

    def test_it_records_the_decision_and_why(self) -> None:
        rows: list[dict] = []
        _tracker(journal=rows.append).cycle({**_lot(), "user_id": "me"}, now_ms=1_000)

        assert rows[0]["decision"] == "pass"
        assert rows[0]["reason"] == "already_high"


class TestItCannotReachADatabase:
    def test_structurally(self) -> None:
        import _importgraph

        assert not _importgraph.reaches(
            "fantabot.application.asta_room", "fantabot.adapters.persistence"
        )


class TestTheUuidTranslation:
    """B1's semantics, kept where the code that performs them now lives.

    These assertions used to sit on `interface.asta._target_of`, which the tracker replaced.
    A tested function nobody calls is worse than an untested one — it reads as covered — so
    the function went and its behaviour is pinned here instead.

    `resolve_ids` re-keys the *ledger*, so `owned` and every walk-away are fantacalcio ids;
    the lot arrives from the raw `auction/<fl>` node and is still a FantaLab uuid. Looking one
    up among the others misses on every lot, silently.
    """

    def test_the_bid_target_is_the_node_uuid_not_the_fantacalcio_id(self) -> None:
        """It goes straight back out in the payload, and the platform refuses a raise naming
        a different lot (`docs/fantalab/06 §10.1`, test 5)."""
        frame = _tracker().cycle(_lot(), now_ms=1_000)

        assert frame.target == "uuid-a1"

    def test_a_known_player_the_plan_did_not_pick_is_not_a_target(self) -> None:
        """The plan prices its own members; everything else is a lot we let go."""
        frame = _tracker().cycle(_lot(uuid="uuid-a2"), now_ms=1_000)

        assert frame.target is None
        assert frame.decision == "hold"

    def test_a_lot_over_the_walkaway_is_a_named_target_that_is_refused(self) -> None:
        """Refusing on price is `decide_bid`'s call, and the frame keeps the target either
        way. Folding a refusal into "not a target" would make a player priced too high
        indistinguishable from one the plan never named, and the heartbeat could no longer
        say which it saw."""
        frame = _tracker().cycle(_lot(price=99), now_ms=1_000)

        assert frame.target == "uuid-a1", "still ours, just too expensive"
        assert frame.decision == "pass"
        assert frame.reason in {"walk_away", "budget", "max_cap"}

    def test_an_uncompletable_rosa_holds_instead_of_raising(self) -> None:
        """`reservations` raises when no rosa can be built from here — too few credits for the
        slots left. That is a state the next sale might undo, so the loop keeps drawing and
        keeps holding rather than ending the evening on it."""
        frame = _tracker(budget_override=0.0).cycle(_lot(), now_ms=1_000)

        assert frame.decision == "hold"
        assert frame.plan == ()


def test_the_frame_carries_every_walkaway_for_the_listone_column() -> None:
    """Showing only the lot's own number tells the operator nothing about what is coming."""
    frame = _tracker().cycle(_lot(), now_ms=1_000)

    assert frame.walkaways
    assert set(frame.walkaways) <= {p.id for p in POOL}


class TestTheBriefIsNotFedFalsehoods:
    """The copilot exists to re-read the state. Handing it an invented one is worse than
    handing it nothing: its judgement degrades and the operator reads the conclusion.

    `schemi_open=0` and `recent=()` were placeholders in the room's wiring, so every brief
    claimed the rosa could field none of the eleven schemi and that nothing had been sold —
    the first false always, the second false after the opening lot.
    """

    def test_the_frame_carries_how_many_schemi_the_rosa_actually_fields(self) -> None:
        frame = _tracker(ledger=[AssignmentEvent("uuid-gk", 10, "us"),
                                 AssignmentEvent("uuid-a1", 20, "us")]).cycle(
            _lot(uuid="uuid-a2"), now_ms=1_000
        )

        assert frame.schemi_open == 1, "a keeper and a striker field the fixture's one schema"

    def test_an_empty_rosa_fields_nothing_and_says_so_truthfully(self) -> None:
        assert _tracker().cycle(_lot(), now_ms=1_000).schemi_open == 0

    def test_the_frame_carries_the_last_sales_as_readable_lines(self) -> None:
        frame = _tracker(ledger=[AssignmentEvent("uuid-gk", 12, "rival")]).cycle(
            _lot(), now_ms=1_000
        )

        assert frame.recent, "the room's tempo, which the brief claimed was always empty"
        assert "Portiere" in frame.recent[0], "named, not a uuid"
        assert "12" in frame.recent[0]

    def test_only_the_last_few_sales_travel(self) -> None:
        """A brief carrying two hundred lines is a brief nobody reads, model included."""
        ledger = [AssignmentEvent(f"uuid-a{i % 2 + 1}", i, "rival") for i in range(20)]
        frame = _tracker(ledger=ledger).cycle(_lot(), now_ms=1_000)

        assert len(frame.recent) <= 3


class TestProvenanceNamesWhatActuallyDecided:
    """`walk-away 77 (floor)` on a number the budget decided is a lie on the one line the
    operator reads before spending. The walk-away is `min(remaining_budget, max(marginal,
    floor))`, so three things can be the binding constraint and the label must say which."""

    def test_the_budget_is_named_when_it_is_the_thing_that_bound(self) -> None:
        frame = _tracker(budget_override=5.0).cycle(_lot(), now_ms=1_000)

        if frame.walk_away is not None:
            assert frame.provenance in {"budget", "marginal", "floor"}
            if frame.walk_away == 5:
                assert frame.provenance == "budget"
