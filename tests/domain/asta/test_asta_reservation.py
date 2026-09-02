"""Rolling re-optimization and the walk-away (reservation) price. Pure and synchronous.

As each player is sold, ``apply_event`` folds the sale into our state (ours -> owned+spent,
anyone's -> taken) and the roster is re-optimized. The reservation for a target is how much
objective value we lose by not securing him — in the value signal's own (credit-like) units,
capped at the remaining budget; a target whose loss makes the roster infeasible is essential
and reserves the whole budget.
"""

from __future__ import annotations

import pytest

from fantabot.domain.asta.legality import SchemaLegality, SlotRule
from fantabot.domain.asta.live import AssignmentEvent
from fantabot.domain.asta.reservation import (
    apply_event,
    opportunistic_walkaway,
    reservations,
    rolling_advisory,
)
from fantabot.domain.asta.roles import MantraPlayer, normalize_roles
from fantabot.domain.asta.state import AstaState, RosterRules
from fantabot.domain.asta.value import NaiveValueModel

RULES = RosterRules(size=3, goalkeeper_roles=frozenset({"POR"}), min_goalkeepers=1, min_movement=2)
MINI = {
    "por-a-a": SchemaLegality(
        nome="por-a-a",
        slots=(
            SlotRule("Por", frozenset({"POR"}), frozenset({"POR"})),
            SlotRule("A", frozenset({"A"}), frozenset({"A"})),
            SlotRule("A2", frozenset({"A"}), frozenset({"A"})),
        ),
    )
}
POOL = [
    MantraPlayer("a1", normalize_roles(["A"])),
    MantraPlayer("a2", normalize_roles(["A"])),
    MantraPlayer("a3", normalize_roles(["A"])),
    MantraPlayer("gk", normalize_roles(["POR"])),
]
TEAMS = {"a1": "X", "a2": "Y", "a3": "Z", "gk": "W"}
PRICES = {"a1": 10.0, "a2": 10.0, "a3": 9.0, "gk": 5.0}
VALUE = NaiveValueModel(
    signals={"a1": 10.0, "a2": 10.0, "a3": 9.0, "gk": 3.0},
    prior_mean=1.0,
    base_variance=4.0,
    no_history_variance=4.0,
)


def _kw() -> dict[str, object]:
    return dict(value=VALUE, prices=PRICES, teams=TEAMS, legality=MINI, rules=RULES, lam=0.0)


def _rolling_kw() -> dict[str, object]:
    """``rolling_advisory`` takes a value *factory*; the constant one is the replay case."""
    return {**{k: v for k, v in _kw().items() if k != "value"}, "value_of": lambda: VALUE}


def test_apply_event_folds_our_purchase_into_owned_and_spent() -> None:
    state = AstaState(total_budget=100.0)
    after = apply_event(state, AssignmentEvent("a1", 12, "me"), our_team_id="me")
    assert after.owned == ("a1",)
    assert after.spent == 12.0
    assert "a1" in after.taken


def test_apply_event_marks_a_rivals_purchase_as_taken_only() -> None:
    state = AstaState(total_budget=100.0)
    after = apply_event(state, AssignmentEvent("a1", 12, "rival"), our_team_id="me")
    assert after.owned == ()
    assert after.spent == 0.0
    assert "a1" in after.taken


def test_reservation_is_higher_for_an_essential_player() -> None:
    state = AstaState(total_budget=100.0)
    _, walkaways = reservations(state, POOL, **_kw())  # type: ignore[arg-type]
    # The keeper is the only POR — losing him makes the roster infeasible -> full budget.
    assert walkaways["gk"] == state.remaining_budget
    # A fungible attacker is worth only its small marginal edge over the alternative.
    assert 0 < walkaways["a1"] < state.remaining_budget


def test_walkaways_are_never_negative() -> None:
    # The greedy builder is a heuristic, so base - alt can go negative on real pools; a
    # walk-away must never be below zero.
    state = AstaState(total_budget=100.0)
    _, walkaways = reservations(state, POOL, **_kw())  # type: ignore[arg-type]
    assert all(walkaway >= 0 for walkaway in walkaways.values())


def test_rolling_replans_when_a_target_is_taken_by_a_rival() -> None:
    state = AstaState(total_budget=100.0)
    events = [AssignmentEvent("a1", 10, "rival")]  # a rival buys a1
    steps = list(rolling_advisory(state, POOL, events, our_team_id="me", **_rolling_kw()))  # type: ignore[arg-type]
    assert len(steps) == 1
    _, _, result, _ = steps[0]
    assert "a1" not in result.optimal.player_ids  # a1 is gone, the plan moved on


def test_the_advisory_re_reads_the_value_each_cycle() -> None:
    """A row written mid-asta must reach the next walk-away, not the next restart.

    ``news_sentiment`` holds a session and never a cached table for exactly this reason: an
    asta runs for hours, and a player ruled out at 21:00 should stop being a target at
    21:01. That guarantee is worth nothing if the advisory snapshots its value model once.
    """
    # A wider bench than POOL: two rivals buy, and the roster must still be completable.
    pool = [*POOL, MantraPlayer("a4", normalize_roles(["A"])), MantraPlayer("a5", normalize_roles(["A"]))]
    prices = {**PRICES, "a4": 10.0, "a5": 10.0}
    teams = {**TEAMS, "a4": "P", "a5": "Q"}

    def _model(a5_worth: float) -> NaiveValueModel:
        return NaiveValueModel(
            signals={"a1": 10.0, "a2": 10.0, "a3": 9.5, "a4": 9.0, "a5": a5_worth, "gk": 3.0},
            prior_mean=1.0,
            base_variance=4.0,
            no_history_variance=4.0,
        )

    # Between the first sale and the second, a5's reading improves — as a mid-asta
    # news fetch would do. A snapshotted model would never see it.
    models = [_model(0.1), _model(99.0)]
    calls: list[int] = []

    def value_of() -> NaiveValueModel:
        calls.append(len(calls))
        return models[min(len(calls) - 1, 1)]

    events = [AssignmentEvent("a1", 10, "rival"), AssignmentEvent("a2", 10, "rival")]
    steps = list(
        rolling_advisory(
            AstaState(total_budget=100.0), pool, events,
            our_team_id="me", value_of=value_of,
            prices=prices, teams=teams, legality=MINI, rules=RULES, lam=0.0,
        )
    )

    assert len(calls) == 2, "the value model was snapshotted instead of re-read"
    assert "a5" not in steps[0][2].optimal.player_ids
    assert "a5" in steps[1][2].optimal.player_ids


def test_n_targets_still_defaults_to_five() -> None:
    """The default is load-bearing beyond convenience.

    `_cycle_calls` in `test_asta_cycle_cost.py` passes no `n_targets`, so the pinned
    500,000-call ceiling — the tripwire that catches a reverted P10 optimisation — is
    measuring whatever this default is. Widening the *type* to accept `None` is safe;
    moving the *default* silently re-points that ceiling at a 3x heavier cycle.
    """
    import inspect

    assert inspect.signature(reservations).parameters["n_targets"].default == 5


def test_none_prices_every_unowned_member_of_the_plan() -> None:
    """Five walk-aways out of a thirty-man plan is why `asta bid` held on most lots.

    The lot on the block is whatever the room calls next, in arbitrary order. A plan
    priced five deep answers "not a target" for the other twenty-five, which reads
    exactly like a decision not to chase them.
    """
    state = AstaState(total_budget=100.0)

    _, five = reservations(state, POOL, n_targets=2, **_kw())  # type: ignore[arg-type]
    plan, everything = reservations(state, POOL, n_targets=None, **_kw())  # type: ignore[arg-type]

    unowned = [pid for pid in plan.optimal.player_ids if pid not in state.owned]
    assert len(five) == 2, "the capped call still caps"
    assert set(everything) == set(unowned)
    assert len(everything) > len(five), "the fixture must be able to tell the two apart"


def test_none_prices_nothing_extra_beyond_the_plan() -> None:
    """`None` means "the whole plan", not "the whole pool" — a walk-away for a player
    the optimiser did not choose would be a number nobody should act on."""
    state = AstaState(total_budget=100.0)

    plan, everything = reservations(state, POOL, n_targets=None, **_kw())  # type: ignore[arg-type]

    assert set(everything) <= set(plan.optimal.player_ids)
    assert set(everything).isdisjoint({p.id for p in POOL} - set(plan.optimal.player_ids))


def test_an_owned_player_is_never_given_a_walkaway() -> None:
    """We do not bid on what we hold; that stays true when the cap is lifted."""
    state = AstaState(owned=frozenset({"gk"}), total_budget=100.0)

    _, everything = reservations(state, POOL, n_targets=None, **_kw())  # type: ignore[arg-type]

    assert "gk" not in everything


class TestTheOpportunisticCeiling:
    """A lot the plan did not name is not a lot to let go at any price.

    `reservations` prices only the plan's own members, so everyone else came back with no
    walk-away at all and the room held whatever the price was. The optimiser rejecting a
    player at his *book* price says nothing about him at a third of it.
    """

    @staticmethod
    def _kw(**over: object) -> dict[str, object]:
        base: dict[str, object] = dict(
            owned_players=[], prices=PRICES, plan=["a1", "a2", "gk"], owned=[],
            legality=MINI, rules=RULES, max_cap=99, beta=0.6, min_book=5,
        )
        return {**base, **over}

    def test_a_deep_discount_is_priced_from_the_book_alone(self) -> None:
        """No re-solve: `asta room` already spends 1 + |plan| solves a cycle."""
        assert opportunistic_walkaway(MantraPlayer("a3", frozenset({"A"})), **self._kw()) == 5

    def test_a_player_the_plan_did_pick_is_left_to_reservations(self) -> None:
        assert opportunistic_walkaway(MantraPlayer("a1", frozenset({"A"})), **self._kw()) is None

    def test_a_player_we_already_own_is_never_chased(self) -> None:
        kw = self._kw(owned=["a3"], plan=["a1", "a2", "a3"])
        assert opportunistic_walkaway(MantraPlayer("a3", frozenset({"A"})), **kw) is None

    def test_an_immaterial_book_price_is_not_a_bargain(self) -> None:
        """`planning_cost` is 1 for every player with no observed sale, so without this floor
        every unpriced riserva reads as a 1-credit bargain."""
        kw = self._kw(min_book=20)
        assert opportunistic_walkaway(MantraPlayer("a3", frozenset({"A"})), **kw) is None

    def test_zero_beta_disables_the_path_entirely(self) -> None:
        kw = self._kw(beta=0.0)
        assert opportunistic_walkaway(MantraPlayer("a3", frozenset({"A"})), **kw) is None

    def test_it_never_exceeds_the_plans_own_dearest_outstanding_target(self) -> None:
        """The share gate. A price the plan has already shown it can absorb in one lot — and
        it falls on its own as the evening spends the budget down."""
        rich = {**PRICES, "a3": 400.0}
        kw = self._kw(prices=rich, min_book=1)

        assert opportunistic_walkaway(MantraPlayer("a3", frozenset({"A"})), **kw) == 10

    def test_it_never_exceeds_the_max_cap(self) -> None:
        """`max_bid` is the only thing between a bargain and a rosa we cannot complete;
        `docs/fantalab/01:142` says the server enforces nothing."""
        kw = self._kw(prices={**PRICES, "a3": 400.0, "a1": 400.0}, min_book=1, max_cap=7)

        assert opportunistic_walkaway(MantraPlayer("a3", frozenset({"A"})), **kw) == 7

    def test_a_bargain_that_would_break_the_keeper_band_is_refused(self) -> None:
        """`max_bid` reserves credits and checks no role at all. This band holds one keeper,
        and a second one is a rosa that cannot be fielded at any price."""
        kw = self._kw(
            owned_players=[MantraPlayer("gk", frozenset({"POR"}))],
            owned=["gk"], plan=["gk", "a1", "a2"],
            prices={**PRICES, "gk2": 40.0},
        )

        assert opportunistic_walkaway(MantraPlayer("gk2", frozenset({"POR"})), **kw) is None

    def test_a_bargain_that_would_break_the_movement_band_is_refused(self) -> None:
        kw = self._kw(
            owned_players=[MantraPlayer("a1", frozenset({"A"})), MantraPlayer("a2", frozenset({"A"}))],
            owned=["a1", "a2"], plan=["a1", "a2", "gk"],
            prices={**PRICES, "a3": 40.0},
        )

        assert opportunistic_walkaway(MantraPlayer("a3", frozenset({"A"})), **kw) is None

    def test_a_role_no_schema_has_a_slot_for_is_refused(self) -> None:
        """Not `fieldable_schemi`: `can_field` matches a *full* XI, so a partial rosa fields
        nothing and that gate would refuse every bargain all evening."""
        kw = self._kw(prices={**PRICES, "w": 40.0})

        assert opportunistic_walkaway(MantraPlayer("w", frozenset({"W"})), **kw) is None


class TestLotCeilingGeneralizesToTheLotOnTheBlock:
    """`lot_ceiling` says nothing about whether `player_id` is a plan member.

    This is the golden-pool regression for `SPEC.md` §2.A: the plan never sees the price of
    the lot in front of it. Malen (fantacalcio id 5585) was live on 2026-09-01 with the
    highest mean value in the entire pool and was never priced — `reservations()` prices only
    its own optimal roster, so a lot outside it has no key in `walkaways` and holds at any
    price. Reproduced here on the committed golden pool (`tests/golden`, pinned to
    2026-08-28) rather than the live database, so the regression runs in the default
    socket-free tier.
    """

    @staticmethod
    def _world():  # type: ignore[no-untyped-def]
        from _golden import PINNED_TODAY, load_clearing_sales, load_quotazioni, load_sentiment

        from fantabot.application.asta_planner import build_plan_inputs
        from fantabot.domain.asta.prices import Sale, mean_prices

        return build_plan_inputs(
            load_quotazioni(),
            mean_prices(Sale(pid, price) for pid, price in load_clearing_sales()),
            load_sentiment(),
            as_of=PINNED_TODAY,
            tilt_k=0.25,
        )

    @classmethod
    def setup_class(cls) -> None:
        from fantabot.domain.asta.reservation import lot_ceiling

        cls.world = cls._world()
        cls.state = AstaState(total_budget=500.0)
        plan, _ = reservations(
            cls.state, cls.world.pool, value=cls.world.value, prices=cls.world.prices,
            teams=cls.world.teams, legality=cls.world.legality, lam=0.3, n_targets=None,
        )
        cls.baseline = plan.optimal.objective
        cls.in_plan = frozenset(plan.optimal.player_ids)
        cls.ceiling = staticmethod(
            lambda player_id, hard_cap=500: lot_ceiling(
                cls.state, cls.world.pool, value=cls.world.value, prices=cls.world.prices,
                teams=cls.world.teams, legality=cls.world.legality, lam=0.3,
                baseline=cls.baseline, player_id=player_id, hard_cap=hard_cap,
            )
        )

    def test_the_pinned_baseline_has_not_drifted(self) -> None:
        """Every other assertion in this class is measured against this number. If it moves,
        the golden fixtures moved and the numbers below need re-measuring, not patching."""
        assert self.baseline == pytest.approx(2251.8, abs=0.1)

    def test_malen_is_absent_from_the_plan_today(self) -> None:
        """The bug, pinned: `reservations()` never names him, regardless of how good he is."""
        assert "5585" not in self.in_plan

    def test_malen_is_still_priced_even_though_the_plan_never_named_him(self) -> None:
        """The fix: `lot_ceiling` prices the lot on the block whether or not `reservations()`
        did. Measured live at book 40 the objective gain was +577; reproduced here on the
        golden pool at +595.7 — both comfortably clear a ceiling of 40."""
        assert self.ceiling("5585") >= 40

    @pytest.mark.parametrize(
        ("name", "player_id", "expected"),
        [
            ("Bremer", "2788", 27),
            ("Akanji", "4159", 19),
            ("Rrahmani", "4409", 15),
            ("Svilar", "5841", 38),
            ("Wesley", "7181", 33),
        ],
    )
    def test_an_already_planned_member_still_gets_a_real_ceiling(
        self, name: str, player_id: str, expected: int
    ) -> None:
        """The regression the old, baseline-relative margin failed: forcing an already-
        planned member back in at a nearby price used to return a ceiling of exactly 0 for
        every one of these five, because `0.15 * baseline(2251.8) = 337.8` dwarfs the objective
        swing from a single player's price change. Scaling the margin to the player's own
        value fixes it — every ceiling below is real and non-zero, and sits a few credits
        *under* book, not over it (paying a premium to keep someone we could already afford is
        `--ceiling-alpha`'s job, not this function's).
        """
        assert player_id in self.in_plan, f"{name} must be a plan member for this test to mean anything"
        assert self.ceiling(player_id) == expected


class TestLotReferencePicksTheRightObjectiveToBeat:
    """`lot_ceiling` called directly with `baseline=plan.optimal.objective` — which is what a
    naive generalization to "every lot, plan member or not" would pass — is wrong for a plan
    member: forcing him back in at his own book price changes nothing else about the roster,
    so the criterion ties `baseline` exactly and can never clear the margin. `lot_reference`
    is the fix: `baseline` unchanged for a lot the plan did not name (it already *is* the
    objective without him), the objective with him forced **out**, re-solved, for one it did.
    """

    @classmethod
    def setup_class(cls) -> None:
        from fantabot.domain.asta.reservation import lot_ceiling, lot_reference

        cls.world = TestLotCeilingGeneralizesToTheLotOnTheBlock._world()
        cls.state = AstaState(total_budget=500.0)
        plan, _ = reservations(
            cls.state, cls.world.pool, value=cls.world.value, prices=cls.world.prices,
            teams=cls.world.teams, legality=cls.world.legality, lam=0.3, n_targets=None,
        )
        cls.baseline = plan.optimal.objective
        cls.plan = plan.optimal.player_ids
        cls.reference = staticmethod(
            lambda player_id: lot_reference(
                cls.state, cls.world.pool, value=cls.world.value, prices=cls.world.prices,
                teams=cls.world.teams, legality=cls.world.legality, lam=0.3,
                baseline=cls.baseline, player_id=player_id, plan=cls.plan,
            )
        )
        cls.ceiling_at = staticmethod(
            lambda player_id, reference, hard_cap=500: lot_ceiling(
                cls.state, cls.world.pool, value=cls.world.value, prices=cls.world.prices,
                teams=cls.world.teams, legality=cls.world.legality, lam=0.3,
                baseline=reference, player_id=player_id, hard_cap=hard_cap,
            )
        )

    def test_an_unplanned_lot_gets_baseline_back_unchanged(self) -> None:
        """He is not in the plan, so `baseline` already *is* the objective without him — no
        second solve is needed, and none is done: `lot_reference` returns the same object."""
        assert "5585" not in self.plan
        assert self.reference("5585") == self.baseline

    def test_seventeen_of_thirty_plan_members_are_pinned_at_the_floor_against_baseline(
        self,
    ) -> None:
        """The bug, pinned on the real pool: comparing a plan member against a total that
        already includes him ties at every feasible price and never clears the margin — not
        an edge case, more than half the live 30-man plan on 2026-08-28."""
        zeros = [pid for pid in self.plan if self.ceiling_at(pid, self.baseline) == 0]
        assert len(zeros) == 17

    def test_the_same_players_get_a_real_ceiling_against_their_own_reference(self) -> None:
        """The fix: re-solve with him excluded instead of comparing against a baseline that
        already assumes he is in it. Not all 17 clear it — 3 (measured: 7602, 5882, 7600) stay
        at 0 even against their own `alt`, because a near-identical substitute genuinely
        exists (`baseline - alt` of 0.14, 1.08, 0.32 against a margin of ~3) — a real "he is
        fungible" answer, not the bug. The other 14 are the regression this test pins.
        """
        zeros_before = [pid for pid in self.plan if self.ceiling_at(pid, self.baseline) == 0]
        assert zeros_before, "the fixture must reproduce the bug for this to mean anything"
        still_zero = {pid for pid in zeros_before if self.ceiling_at(pid, self.reference(pid)) == 0}
        assert still_zero == {"7602", "5882", "7600"}, f"expected only the near-substitutes: {still_zero}"


class TestLotReferenceOnAnEssentialPlayer:
    """The same "essential" convention `reservations()` already uses (`except
    InfeasibleRoster: walkaways[target] = state.remaining_budget`): removing him leaves no
    completable roster at all, so there is no "objective without him" to solve for.
    """

    def test_none_means_no_alternative_exists_not_a_number(self) -> None:
        from fantabot.domain.asta.reservation import lot_reference

        state = AstaState(total_budget=100.0)
        plan, _ = reservations(state, POOL, **_kw())  # type: ignore[arg-type]
        assert "gk" in plan.optimal.player_ids, "the only POR in the pool must be planned"

        reference = lot_reference(
            state, POOL, value=VALUE, prices=PRICES, teams=TEAMS, legality=MINI, rules=RULES,
            baseline=plan.optimal.objective, player_id="gk", plan=plan.optimal.player_ids,
        )
        assert reference is None
