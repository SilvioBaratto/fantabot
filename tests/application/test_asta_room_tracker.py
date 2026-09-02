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
# `400` is the fixture's only real bargain, and he is built to be one. The plan cannot
# afford him — 95 for him plus 10 for the obligatory keeper is 105 of a 100 budget — so he
# is never named, yet owning him at a low price and *then* buying the keeper is a strictly
# better rosa (20 + 5 = 25 against the plan's 20). `300` is the control: also unplanned,
# also under his book price, and buying him only ever swaps mu 15 for mu 9.
#
# `200`'s mu (15, not the flatter 10 an earlier version used) is deliberate too: `lot_ceiling`
# now prices a plan member against the objective with *him* excluded (`14`, keeper + `300`),
# not against a total that already assumes he is in it — see `lot_reference`. A gap of 15 - 9
# clears the margin (`max(1.0, 0.15 * 15) = 2.25`); the earlier 10 - 9 did not, and every
# already-planned member of the fixture held forever regardless of price.
POOL = [
    MantraPlayer("100", normalize_roles(["POR"])),
    MantraPlayer("200", normalize_roles(["A"])),
    MantraPlayer("300", normalize_roles(["A"])),
    MantraPlayer("400", normalize_roles(["A"])),
]
TEAMS = {"100": "W", "200": "X", "300": "Y", "400": "Z"}
PRICES = {"100": 10.0, "200": 40.0, "300": 39.0, "400": 95.0}
NAMES = {"100": "Portiere", "200": "Bomber", "300": "Riserva", "400": "Occasione"}
VALUE = NaiveValueModel(
    signals={"100": 5.0, "200": 15.0, "300": 9.0, "400": 20.0},
    prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
)
BRIDGE = {"uuid-gk": 100, "uuid-a1": 200, "uuid-a2": 300, "uuid-a3": 400}
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
        """`walk-away 77 (ceiling)` — never a fused number nobody can argue with. The retired
        walk-away floor's `marginal`/`floor` split is gone with it; a plan member now prices
        off the same `lot_ceiling` re-solve as any other lot — see `lot_reference`."""
        frame = _tracker().cycle(_lot(), now_ms=1_000)

        assert frame.walk_away is not None
        assert frame.provenance in {"ceiling", "budget"}

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

    def test_a_known_player_the_plan_did_not_pick_is_never_bought_at_his_book_price(
        self,
    ) -> None:
        """This assertion used to read `target is None` at any price at all, which is the
        gap: the optimiser saying "not worth 39" was read as "not worth anything". At book
        the answer is unchanged — we do not buy him — but now it is a stated refusal.
        """
        frame = _tracker().cycle(_lot(uuid="uuid-a2", price=39), now_ms=1_000)

        assert frame.decision != "bid"


class TestALotThePlanDidNotNameIsStillWorthSomething:
    """`reservations` prices only the plan's own members, so every other lot came back
    `walk_away=None` and the room held at any price — a player the book puts at 189 sitting
    at 30 was indistinguishable on screen from one we had decided to let go.

    Both unplanned players are cheap against their book. Only one of them is worth buying,
    and telling them apart is what the re-solve is for.
    """

    def test_a_lot_the_plan_could_not_afford_is_taken_when_the_room_marks_it_down(
        self,
    ) -> None:
        """400 costs 95 and the keeper 10, so a 100-credit plan can never name him. Owning
        him at 5 and buying the keeper after is 25 against the plan's 20.

        `bargain_share` is named rather than defaulted: at the production 0.10 this fixture's
        100-credit purse allows 10, and the aggregate cap -- not the objective -- would be
        what set the ceiling. This test is about the objective's answer.
        """
        frame = _tracker(bargain_share=0.40).cycle(
            _lot(uuid="uuid-a3", price=5), now_ms=1_000
        )

        assert frame.decision == "bid"
        assert frame.target == "uuid-a3", "the payload must carry the node uuid, not 400"
        assert frame.walk_away == 40, "the pre-gate's share cap; the objective allows 90"
        assert frame.provenance == "bargain", "not marginal — no marginal was ever computed"
        assert frame.walkaways["400"] == 40, "or the LISTONE row shows a bid with no ceiling"

    def test_a_discount_the_objective_does_not_want_is_refused(self) -> None:
        """**The test the price-map heuristic fails.** 300 is unplanned and 5 is deep under
        his book of 39, so every cheap gate says bargain — and buying him only ever swaps
        the plan's mu 15 for his mu 9. Measured on the live pool this is not an edge case:
        of the 53 lots the pre-gate admitted, the re-solve refused 33 and lowered 11.
        """
        frame = _tracker().cycle(_lot(uuid="uuid-a2", price=5), now_ms=1_000)

        assert frame.decision == "hold"
        assert frame.target is None

    def test_the_ceiling_binds_and_the_lot_is_refused_above_it(self) -> None:
        """Named and refused, not silently unnamed: the operator has to be able to tell a
        price we walked away from apart from a player we never considered."""
        frame = _tracker(bargain_share=0.40).cycle(
            _lot(uuid="uuid-a3", price=40), now_ms=1_000
        )

        assert frame.target == "uuid-a3"
        assert frame.decision == "pass"
        assert frame.reason == "walk_away"

    def test_zero_beta_restores_the_plan_only_behaviour(self) -> None:
        frame = _tracker(bargain_beta=0.0).cycle(_lot(uuid="uuid-a3", price=5), now_ms=1_000)

        assert frame.target is None
        assert frame.decision == "hold"

    def test_a_cheap_lot_is_not_a_bargain_however_deep_the_discount(self) -> None:
        """`planning_cost` returns 1 for every player with no observed sale, so without a
        materiality floor every unpriced riserva reads as a bargain at 1 credit."""
        frame = _tracker(bargain_min_book=96).cycle(
            _lot(uuid="uuid-a3", price=5), now_ms=1_000
        )

        assert frame.decision == "hold"

    def test_the_ceiling_is_solved_once_per_state_not_once_per_poll(self) -> None:
        """A lot lives 20-60 s at a 2 s poll. Re-solving it thirty times for the same answer
        is how a per-lot solve becomes unaffordable; the ceiling depends on the state and on
        nothing that moves between polls, so it is cached against the state."""
        import fantabot.application.asta_room as room

        calls = []
        real = room.lot_ceiling
        room.lot_ceiling = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            tracker = _tracker(bargain_share=0.40)
            for price in (5, 6, 7, 8):
                frame = tracker.cycle(_lot(uuid="uuid-a3", price=price), now_ms=1_000)
        finally:
            room.lot_ceiling = real

        assert frame.decision == "bid", "still bidding, just not re-deciding from scratch"
        assert len(calls) == 1, f"one solve for four polls of one lot, got {len(calls)}"

    def test_the_cached_ceiling_is_thrown_away_when_a_sale_moves_the_state(self) -> None:
        """The other half of the memo, and the dangerous half. The ceiling is a function of
        the rosa, the purse and the taken set; a sale changes all three, and re-using the
        number computed before it would price the lot against a plan that no longer exists.

        It matters more since C1: the scan can cost `hard_cap` solves where the bisection
        cost seven, so the temptation to widen the cache key is real and the cost of widening
        it wrongly is a bid at a stale price.
        """
        import fantabot.application.asta_room as room

        ledger: list[AssignmentEvent] = []
        calls: list[int] = []
        real = room.lot_ceiling
        room.lot_ceiling = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            tracker = _tracker(ledger=ledger, bargain_share=0.40)
            tracker.cycle(_lot(uuid="uuid-a3", price=5), now_ms=1_000)
            assert len(calls) == 1
            ledger.append(AssignmentEvent("uuid-a1", 20, "rival"))
            tracker.cycle(_lot(uuid="uuid-a3", price=5), now_ms=2_000)
        finally:
            room.lot_ceiling = real

        assert len(calls) == 2, "a sale landed; the ceiling must be re-solved, not re-used"

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
    """`walk-away 77 (budget)` on a number the re-solve decided is a lie on the one line the
    operator reads before spending. `lot_ceiling` is scanned up to `hard_cap`, itself bounded
    by `credits_left` — so the label must say when the purse itself is what bound, not the
    re-solve, and `ceiling`/`bargain` otherwise."""

    def test_the_budget_is_named_when_it_is_the_thing_that_bound(self) -> None:
        frame = _tracker(budget_override=5.0).cycle(_lot(), now_ms=1_000)

        if frame.walk_away is not None:
            assert frame.provenance in {"budget", "ceiling", "bargain"}
            if frame.walk_away == 5:
                assert frame.provenance == "budget"


# -- C3: the evening's one bargain purse ------------------------------------------------
#
# The fixture above cannot show a cap *binding*: with `size=2` a single bargain fills the
# movement band, so the second lot is refused on roles and the cap never gets a word in.
# This one holds three (one keeper, two movement) and two players the plan cannot name, so
# the only thing that can tell the two bargains apart is what the first one cost.
#
# `500` is deliberately the weaker of the two -- book 90 against 95, mu 19 against 20 -- so
# that after winning `400` he is still a genuine bargain on the objective's own terms, and a
# refusal can only be the aggregate cap.
CAP_POOL = [*POOL, MantraPlayer("500", normalize_roles(["A"]))]
CAP_PRICES = {**PRICES, "500": 90.0}
CAP_TEAMS = {**TEAMS, "500": "V"}
CAP_NAMES = {**NAMES, "500": "Occasione2"}
CAP_BRIDGE = {**BRIDGE, "uuid-a4": 500}
CAP_VALUE = NaiveValueModel(
    signals={"100": 5.0, "200": 10.0, "300": 9.0, "400": 20.0, "500": 19.0},
    prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
)
CAP_RULES = RosterRules(size=3, min_goalkeepers=1, min_movement=2)
#: We won `400` as a bargain at 40. 40% of the 100-credit purse is exactly spent.
WON_A_BARGAIN = [AssignmentEvent("uuid-a3", 40, "us")]


def _cap_tracker(share: float, ledger=()):  # type: ignore[no-untyped-def]
    return RoomTracker(
        seat=SEAT, bridge=CAP_BRIDGE, pool=CAP_POOL, value=CAP_VALUE, prices=CAP_PRICES,
        teams=CAP_TEAMS, legality=SCHEMI, names=CAP_NAMES, rules=CAP_RULES,
        budget=100.0, lam=0.0,
        ledger=lambda: list(ledger), journal=lambda _row: None,
        counter_time=10, counter_time_first=20, bargain_share=share,
    )


class TestTheEveningHasOneBargainPurse:
    """Each bargain is approved against the plan on its own, and "better than the plan" does
    not compose: two lots that each improve the rosa can, bought together, leave a purse that
    buys neither of the players the second re-solve assumed we would still afford. Nothing
    else in the loop notices -- `max_bid` reserves credits per remaining slot and is happy to
    see them go on anything, and `docs/fantalab/01:142` says the server caps nothing at all.
    """

    @staticmethod
    def _raise_on_400(tracker, ledger, then):  # type: ignore[no-untyped-def]
        """Live order of events, which is the whole point: we raise on `400` while he is
        still on the block, and only afterwards does the ledger say who won him.

        Bidding first is not a detail of the fixture. The provenance is remembered at the
        moment of the raise, because that is the only moment we know *why*; the ledger, read
        whole every cycle, then supplies the price and the buyer. Handing the tracker a
        finished ledger would skip the half of the join it has to do itself.
        """
        frame = tracker.cycle(_lot(uuid="uuid-a3", price=5), now_ms=1_000)
        assert frame.decision == "bid" and frame.provenance == "bargain", (
            f"the fixture must actually raise on 400: got {frame.decision}/{frame.note}"
        )
        ledger.extend(then)
        return frame

    def test_the_second_bargain_is_refused_once_the_first_has_spent_the_purse(self) -> None:
        """**The cap binding.** Same tracker, same lot, same ledger, same state -- the only
        difference between bidding and holding is a 40-credit bargain already won.
        """
        ledger: list[AssignmentEvent] = []
        tracker = _cap_tracker(0.40, ledger=ledger)
        self._raise_on_400(tracker, ledger, WON_A_BARGAIN)

        frame = tracker.cycle(_lot(uuid="uuid-a4", price=5), now_ms=2_000)

        assert frame.decision == "hold"
        assert frame.target is None
        assert frame.bargain_spent == 40
        assert frame.bargain_allowance == 0
        assert frame.note is not None and "40/40" in frame.note, (
            "a cap the operator cannot see is one he finds out about by not understanding "
            f"why a bid did not go in, so the line has to carry the arithmetic; "
            f"got {frame.note!r}"
        )

    def test_the_same_lot_is_taken_when_the_purse_still_has_room(self) -> None:
        """The control, and the proof that the refusal above is the cap and not the roles,
        the band, the budget or the objective. Everything is identical but the share."""
        ledger: list[AssignmentEvent] = []
        tracker = _cap_tracker(0.80, ledger=ledger)
        self._raise_on_400(tracker, ledger, WON_A_BARGAIN)

        frame = tracker.cycle(_lot(uuid="uuid-a4", price=5), now_ms=2_000)

        assert frame.decision == "bid"
        assert frame.provenance == "bargain"
        assert frame.bargain_spent == 40
        assert frame.bargain_allowance == 40

    def test_a_lot_we_never_raised_on_does_not_count_against_the_cap(self) -> None:
        """A win the *plan* named is not bargain spend. Without the join through the
        remembered provenance the cap would charge the evening for its own plan, and one
        expensive planned lot would switch the opportunistic path off for good.
        """
        tracker = _cap_tracker(0.40, ledger=WON_A_BARGAIN)

        frame = tracker.cycle(_lot(uuid="uuid-a4", price=5), now_ms=1_000)

        assert frame.bargain_spent == 0, "we own 400, but this process never raised on him"
        assert frame.bargain_allowance == 40

    def test_a_bargain_we_bid_on_and_lost_costs_nothing(self) -> None:
        """The cap is enforced against what we actually *spent*, not what we chased. A lot
        we were outbid on never enters `owned`, so it never enters the total."""
        ledger: list[AssignmentEvent] = []
        tracker = _cap_tracker(0.40, ledger=ledger)
        self._raise_on_400(tracker, ledger, [AssignmentEvent("uuid-a3", 40, "rival")])

        after = tracker.cycle(_lot(uuid="uuid-a4", price=5), now_ms=2_000)

        assert after.bargain_spent == 0
        assert after.bargain_allowance == 40

    def test_a_zero_share_switches_the_opportunistic_path_off_entirely(self) -> None:
        """A second, independent off-switch beside `--bargain-beta 0`. The two guard
        different things -- one the per-lot discount, one the evening's total -- and an
        operator who wants the plan and nothing else should not have to know which."""
        frame = _cap_tracker(0.0).cycle(_lot(uuid="uuid-a3", price=5), now_ms=1_000)

        assert frame.decision == "hold"
        assert frame.target is None
        assert frame.bargain_allowance == 0

    def test_the_cap_is_checked_before_anything_is_solved(self) -> None:
        """It is the only free gate of the three, so it goes first. A cap that costs a
        re-solve to discover is a cap that costs the evening its poll budget."""
        import fantabot.application.asta_room as room

        calls: list[int] = []
        real = room.lot_ceiling
        room.lot_ceiling = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            _cap_tracker(0.0).cycle(_lot(uuid="uuid-a3", price=5), now_ms=1_000)
        finally:
            room.lot_ceiling = real

        assert calls == []


class TestTwoWinsLandingInTheSamePollAreStillSafe:
    """Task 1.2's ledger-settlement-lag test (`tasks/plan.md` §2, risk row 3): the room's own
    ledger read can lag a raise that already won, so two decisions made a poll apart can each
    look individually justified against a purse that has not yet caught up with the other.
    That gap is not closed here — `bargain_allowance` already guards the case where we made
    both decisions ourselves (`TestTheEveningHasOneBargainPurse`, above) — this pins the
    fallback for when it is not: once the ledger *does* catch up and shows both wins landing
    in the same poll, at once, `cycle` must fold both correctly and never turn an already-bad
    purse into a crash or a silent overspend. `max_bid` reserving credits per remaining slot
    and `reservations()`'s own `InfeasibleRoster` are what already do this — nothing new is
    added; this is the proof they still hold once every lot goes through the same re-solve.
    """

    def test_both_wins_are_folded_not_just_the_first(self) -> None:
        """The state a decision is made against has to be the state *after* both, not
        whichever one `resolve_ids` happens to see first."""
        ledger = [AssignmentEvent("uuid-a3", 90, "us"), AssignmentEvent("uuid-a4", 90, "us")]
        frame = _cap_tracker(0.40, ledger=ledger).cycle(_lot(uuid="uuid-a2", price=5), now_ms=1_000)

        assert frame.credits_left == 100 - 90 - 90
        assert set(frame.owned) == {"400", "500"}

    def test_a_purse_two_lag_won_bargains_already_broke_holds_instead_of_overspending(
        self,
    ) -> None:
        """`400` and `500` are only ever bargains, never plan members (their combined book —
        185 — leaves nothing for the mandatory keeper). Landing both at once spends 180 of
        100 credits before the keeper is even priced, which no single decision here chose —
        each was a separate poll's answer, made before the other was visible. The keeper slot
        cannot be filled from what's left, so `reservations()` must raise `InfeasibleRoster`
        and `cycle` must hold, not ramp a bid past a budget that is already negative.
        """
        ledger = [AssignmentEvent("uuid-a3", 90, "us"), AssignmentEvent("uuid-a4", 90, "us")]
        frame = _cap_tracker(0.40, ledger=ledger).cycle(_lot(uuid="uuid-gk"), now_ms=1_000)

        assert frame.decision == "hold"
        assert frame.plan == ()
        assert frame.credits_left < 0, "the lag already overspent; the guard is what happens next"


class TestAPassedLotIsAttributedBeforeTheFold:
    """Task 2.3: `attribute_passed_lots` runs on every cycle's ledger, after `resolve_ids` (so
    `price_of` sees fantacalcio ids, matching `self._prices`) and before `apply_event`. Neither
    parameter is required — `asta bid` never has them (its own docstring) — so both default to
    inert rather than crashing a cycle that doesn't supply them.
    """

    def test_a_stood_raise_the_admin_passed_lands_in_our_own_owned_set(self) -> None:
        # uuid-gk / "100" reads price 10 from PRICES — the reattributed price prefers that
        # observed clearing price over the MIN_BID fallback.
        ledger = [AssignmentEvent("uuid-gk", 0, None, "our-uid")]
        frame = _tracker(
            ledger=ledger, admin_user_id="admin-uid", seat_by_user={"our-uid": "us"},
        ).cycle(_lot(), now_ms=1_000)

        assert frame.owned == ("100",)
        assert frame.credits_left == 90

    def test_an_admin_stamped_skip_is_never_reattributed_to_us(self) -> None:
        ledger = [AssignmentEvent("uuid-gk", 0, None, "admin-uid")]
        frame = _tracker(
            ledger=ledger, admin_user_id="admin-uid", seat_by_user={"admin-uid": "us"},
        ).cycle(_lot(), now_ms=1_000)

        assert frame.owned == ()
        assert frame.credits_left == 100

    def test_a_rivals_passed_lot_is_removed_from_the_pool_not_credited_to_us(self) -> None:
        """`seat_by_user` covers every held seat, ours included — a rival's reclaimed lot
        must vanish from the pool the same way ours does, or the plan optimizes around a
        player the room has actually removed."""
        ledger = [AssignmentEvent("uuid-a2", 0, None, "rival-uid")]
        frame = _tracker(
            ledger=ledger, admin_user_id="admin-uid", seat_by_user={"rival-uid": "Y"},
        ).cycle(_lot(), now_ms=1_000)

        assert frame.owned == (), "not ours"
        assert "300" not in frame.plan, "taken, so no longer a candidate the plan can pick"

    def test_neither_parameter_is_required(self) -> None:
        """`asta bid` never has these — `RoomTracker` degrades to no reattribution, not a
        crash, exactly today's behaviour."""
        ledger = [AssignmentEvent("uuid-gk", 0, None, "our-uid")]
        frame = _tracker(ledger=ledger).cycle(_lot(), now_ms=1_000)

        assert frame.owned == ()
