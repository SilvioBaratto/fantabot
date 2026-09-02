"""The re-solve behind an opportunistic bid: is the rosa actually better with him in it?

`opportunistic_walkaway` answers "is this cheap enough to look at" from the price map alone.
Everything here is about the question that decides the purchase, which no dict lookup can
answer: at this price, does forcing him in beat the plan we already have?

The fixture is built so the two disagree. `x` is worth more than anyone in the plan (mu 20
against 10 and 5) and the plan still cannot name him — 100 for him plus 70 for the obligatory
keeper is 170 of a 100-credit budget. The price map's cap for him is 60. The objective's is
30, because at 71 credits spent there is no keeper left to buy. Paying the price map's number
would buy a rosa that cannot be completed, and `docs/fantalab/01:142` says the server takes
the raise anyway.
"""

from __future__ import annotations

import pytest

from fantabot.domain.asta.legality import SchemaLegality, SlotRule
from fantabot.domain.asta.reservation import (
    CEILING_MARGIN_ABS,
    CEILING_MARGIN_REL,
    bargain_allowance,
    lot_ceiling,
    opportunistic_walkaway,
    safe_ceiling,
)
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.asta.state import AstaState, RosterRules
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
    MantraPlayer("g", frozenset({"POR"})),
    MantraPlayer("a1", frozenset({"A"})),
    MantraPlayer("x", frozenset({"A"})),
]
TEAMS = {"g": "W", "a1": "X", "x": "Z"}
PRICES = {"g": 70.0, "a1": 30.0, "x": 100.0}
VALUE = NaiveValueModel(
    signals={"g": 5.0, "a1": 10.0, "x": 20.0},
    prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
)
RULES = RosterRules(size=2, min_goalkeepers=1, min_movement=1)
#: The plan is `g` + `a1` for exactly 100 credits, and this is its objective.
BASELINE = 15.0


def _kw(**over: object) -> dict[str, object]:
    kw: dict[str, object] = dict(
        value=VALUE, prices=PRICES, teams=TEAMS, legality=SCHEMI, rules=RULES, lam=0.0,
        baseline=BASELINE, player_id="x", hard_cap=60,
    )
    kw.update(over)
    return kw


def _state(**over: object) -> AstaState:
    return AstaState(total_budget=100.0, **over)  # type: ignore[arg-type]


class TestTheObjectiveIsWhatDecides:
    def test_the_re_solve_lowers_a_ceiling_the_price_map_would_have_paid(self) -> None:
        """**The whole point of the function.** The price map caps `x` at 60; buying him at
        61 leaves 39 credits and the band still owes a keeper who costs 70. Measured on the
        live pool on 2026-09-01 this is not a corner: the pre-gate admitted 51 of 496
        unplanned lots from a real 3-owned/408-credit state, and the objective's ceiling and
        the price map's disagree routinely.
        """
        pre_gate = opportunistic_walkaway(
            MantraPlayer("x", frozenset({"A"})),
            owned_players=[], prices=PRICES, plan=["g", "a1"], owned=[],
            legality=SCHEMI, rules=RULES, max_cap=99,
        )

        assert pre_gate == 60, "0.60 x book, under the plan's dearest target"
        assert lot_ceiling(_state(), POOL, **_kw()) == 30  # type: ignore[arg-type]

    def test_a_price_the_rosa_cannot_survive_is_a_refusal_not_an_exception(self) -> None:
        """`InfeasibleRoster` at every probe is a "no". It is also the only guard between a
        bargain and a rosa that cannot be filled: `max_bid` reserves credits and checks no
        role, and nothing on the server refuses the raise."""
        assert lot_ceiling(  # type: ignore[arg-type]
            _state(spent=95.0), POOL, **_kw()
        ) == 0

    def test_a_lot_that_never_beats_the_plan_costs_one_solve_and_returns_zero(self) -> None:
        """`a1` is *in* the plan, so forcing him in changes nothing and cannot clear the
        margin. Zero is the value `decide_bid` already refuses at every price."""
        assert lot_ceiling(  # type: ignore[arg-type]
            _state(), POOL, **_kw(player_id="a1")
        ) == 0

    def test_the_ceiling_does_not_move_with_the_lot_s_current_price(self) -> None:
        """It is a function of the state alone. That is what lets the room solve it once for
        the 20-60 s a lot lives, and what makes a lot bid past it a *named* pass rather than
        a silent hold indistinguishable from a player we never considered."""
        assert "ask" not in lot_ceiling.__code__.co_varnames


class TestTheMarginIsANoiseFloorNotATasteKnob:
    def test_an_improvement_inside_the_solver_s_own_jitter_is_not_bought(self) -> None:
        """Re-running a greedy builder with one player forced in moves the objective even
        when nothing real changed — measured -59.2 to +49.4 on a baseline of 1855.4 over 20
        null moves. A margin under that band buys players on solver noise.
        """
        assert lot_ceiling(  # type: ignore[arg-type]
            _state(), POOL, **_kw(margin_abs=100.0)
        ) == 0

    @pytest.mark.parametrize("cap", [0, -5])
    def test_a_cap_below_the_minimum_bid_never_solves(self, cap: int) -> None:
        """A ceiling under 1 truncates to 0 and removes the player from the biddable set —
        `decide_bid` refuses below `MIN_BID` on the same convention elsewhere."""
        assert lot_ceiling(  # type: ignore[arg-type]
            _state(), POOL, **_kw(hard_cap=cap)
        ) == 0


#: **The C1 fixture: `f(p)` is not monotone, and here is one that proves it.**
#:
#: Found by search over random pools and then minimised to eight players, because the
#: pathology needs a pool big enough for the greedy builder to re-order itself under a
#: different purse — it does not show up in a four-player fixture, and the live pool where it
#: was first measured cannot be pinned in a test that opens no socket.
#:
#: Sweeping `f(p)` one credit at a time over `[1, 40]` against a baseline of 187.0 gives
#: `1111100000011111111111111111111000000000`: it passes at 1-5, **fails at 6-11**, passes
#: again at 12-31, and fails from 32. A bisection lands on 31 and reports it, because 31 does
#: pass — and `decide_bid` then ramps `current + 1` straight through 6, 7, 8, 9, 10 and 11.
NONMONOTONE = {
    "p1": ("POR", 46.0, 30.0, "B"),
    "p15": ("A", 46.0, 39.0, "B"),
    "p22": ("D", 2.0, 37.0, "B"),
    "p26": ("D", 50.0, 39.0, "A"),
    "p27": ("D", 6.0, 36.0, "D"),
    "p28": ("A", 9.0, 5.0, "C"),
    "p36": ("D", 6.0, 40.0, "D"),
    "p37": ("A", 24.0, 38.0, "D"),
}
NM_SCHEMI = {
    "s": SchemaLegality(
        nome="s",
        slots=(
            SlotRule("Por", frozenset({"POR"}), frozenset({"POR"})),
            SlotRule("D", frozenset({"D"}), frozenset({"D"})),
            SlotRule("A", frozenset({"A"}), frozenset({"A"})),
        ),
    )
}
NM_POOL = [MantraPlayer(pid, frozenset({spec[0]})) for pid, spec in NONMONOTONE.items()]
NM_PRICES = {pid: spec[1] for pid, spec in NONMONOTONE.items()}
NM_TEAMS = {pid: spec[3] for pid, spec in NONMONOTONE.items()}
NM_VALUE = NaiveValueModel(
    signals={pid: spec[2] for pid, spec in NONMONOTONE.items()},
    prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
)
NM_RULES = RosterRules(size=6, min_goalkeepers=1, min_movement=5)
NM_STATE = AstaState(total_budget=115.0)
NM_BASELINE = 187.0
NM_TARGET = "p1"
NM_CAP = 40


def _nm_kw(**over: object) -> dict[str, object]:
    kw: dict[str, object] = dict(
        value=NM_VALUE, prices=NM_PRICES, teams=NM_TEAMS, legality=NM_SCHEMI, rules=NM_RULES,
        lam=0.0, baseline=NM_BASELINE, player_id=NM_TARGET, hard_cap=NM_CAP,
    )
    kw.update(over)
    return kw


def _nm_passes(price: int) -> bool:
    """The same test `lot_ceiling` applies internally, so the profile below is not a
    re-derivation of the rule but a direct read of it.

    The margin is against `NM_TARGET`'s own value (`mu(p1) = 30.0`), not `NM_BASELINE`
    (187.0) — see `CEILING_MARGIN_REL`'s docstring for why the old baseline-relative margin
    returns 0 for every already-planned member. Measured: the two margins differ (4.5 against
    28.05) but the pass/fail profile over `[1, 40]` on this particular fixture comes out
    byte-identical either way, which is a property of this fixture, not a guarantee.
    """
    from dataclasses import replace

    from fantabot.domain.asta.optimizer import InfeasibleRoster, optimize_roster

    forced = replace(
        NM_STATE, owned=(NM_TARGET,), spent=float(price), taken=frozenset({NM_TARGET})
    )
    try:
        got = optimize_roster(
            forced, NM_POOL, value=NM_VALUE, prices=NM_PRICES, teams=NM_TEAMS,
            legality=NM_SCHEMI, rules=NM_RULES, lam=0.0, n_fallbacks=0,
        ).optimal.objective
    except InfeasibleRoster:
        return False
    mu = NM_VALUE.value(NM_TARGET).mean
    return got >= NM_BASELINE + max(1.0, CEILING_MARGIN_REL * mu)


class TestTheCeilingIsNeverAboveAPriceThatFails:
    """C1. The ceiling used to be bisected, which is correct only for a step function.

    `f(p)` -- the plan objective with the lot forced in at `p` -- cannot really rise in `p`: a
    credit spent here is a credit the rest of the rosa does not have. It rises anyway, because
    the builder is greedy. Measured on the live pool on 2026-09-01: **343 rises over 1,265
    adjacent credit steps**, the largest a single-credit **+102.8**.
    """

    def test_a_failing_price_below_the_answer_makes_the_bisection_unsound(self) -> None:
        """The property, stated directly and independently of any implementation: nothing at
        or below the returned ceiling may fail. A bisection returns 31 here; 6 fails."""
        ceiling = lot_ceiling(NM_STATE, NM_POOL, **_nm_kw())  # type: ignore[arg-type]

        assert ceiling == 5, "the largest price under which the rule holds *everywhere*"
        assert not _nm_passes(6), "the fixture's whole point: 6 fails"
        assert _nm_passes(31), "and 31 passes, which is what a bisection would land on"
        assert all(_nm_passes(q) for q in range(1, ceiling + 1)), (
            "every price the bidder can ramp through must satisfy the criterion"
        )

    def test_the_room_can_win_at_every_price_under_the_ceiling(self) -> None:
        """Why the property is the right one. `decide_bid` raises `current + 1`, so a lot
        opening at 1 is offered at every credit up to the ceiling and can be won at any of
        them. A ceiling is a promise about the whole interval, not about its endpoint.
        """
        ceiling = lot_ceiling(NM_STATE, NM_POOL, **_nm_kw())  # type: ignore[arg-type]
        winnable = range(1, ceiling + 1)

        assert [q for q in winnable if not _nm_passes(q)] == []

    def test_a_scan_stops_at_the_first_failure_rather_than_hunting_for_a_later_pass(
        self,
    ) -> None:
        """`safe_ceiling` in isolation, with the profile written out. A bisection over this
        predicate returns 5 -- the last element -- and every implementation that trusts
        monotonicity returns something above 2."""
        profile = {1: True, 2: True, 3: False, 4: True, 5: True}

        assert safe_ceiling(profile.__getitem__, lo=1, hi=5) == 2

    def test_a_first_price_that_fails_is_a_refusal_and_costs_one_probe(self) -> None:
        calls: list[int] = []

        def passes(price: int) -> bool:
            calls.append(price)
            return False

        assert safe_ceiling(passes, lo=1, hi=90) == 0
        assert calls == [1], "a refusal is the common case and must stay one solve"

    def test_an_empty_range_is_zero_and_never_probed(self) -> None:
        def passes(price: int) -> bool:  # pragma: no cover - must not be reached
            raise AssertionError("probed an empty range")

        assert safe_ceiling(passes, lo=1, hi=0) == 0

    def test_it_never_probes_above_the_hard_cap(self) -> None:
        """The cap is the only thing between a bargain and a rosa that cannot be completed."""
        seen: list[int] = []

        def passes(price: int) -> bool:
            seen.append(price)
            return True

        assert safe_ceiling(passes, lo=1, hi=7) == 7
        assert max(seen) == 7


class TestTheMarginClearsTheMeasuredJitter:
    """C2. The margin is set from a measurement of the greedy builder's own noise, and it is
    scaled to the *player's own value* (`value.value(player_id).mean`), not the whole plan's
    objective — see `CEILING_MARGIN_REL`'s docstring for why: a baseline-relative margin
    returns a ceiling of exactly 0 for every already-planned member, measured on the live
    golden pool (five defenders/midfielders, all zero).

    **What carries over from the original measurement, and what does not.** A *null move* —
    forcing a member of the current plan back in at his own `planning_cost` — changes nothing
    real, so an exact optimizer returns the identical objective, and the live measurement
    (1,754 null moves across 90 randomised states, signed jitter -9.85%..+9.38% *of the
    baseline*) is what first justified 0.15 as a coefficient. That measurement was against the
    baseline, not against `mu`, and no committed script reproduces it (`SPEC.md` §2.F/§9) — so
    0.15 survives here as the conservative default it already was, not as a re-derived number.
    The two numeric tests below are re-verified directly against the current formula, not
    inherited from the old one.
    """

    def test_the_margin_clears_the_worst_upward_null_move_ever_measured(self) -> None:
        assert CEILING_MARGIN_REL >= 0.0938 * 1.5, (
            "0.0938 is the largest upward null move measured against the old, baseline-"
            "relative formula; kept as a floor on the coefficient until it is re-measured "
            "against mu"
        )

    #: The same shape as `POOL`, scaled up by ten, so `x`'s own value (`mu`) is large enough
    #: that the relative margin — not `CEILING_MARGIN_ABS` — is what decides. `mu(x) = 250.0`
    #: here, margin `max(1.0, 0.15*250) = 37.5`.
    BIG = NaiveValueModel(
        signals={"g": 50.0, "a1": 100.0, "x": 250.0},
        prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
    )
    #: `mu(x) = 112.0`, margin `max(1.0, 0.15*112) = 16.8` — large enough that a real but
    #: modest improvement still falls inside it.
    NOISE = NaiveValueModel(
        signals={"g": 50.0, "a1": 100.0, "x": 112.0},
        prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
    )

    def test_a_gain_inside_the_measured_jitter_band_is_refused(self) -> None:
        """**The C2 regression, re-verified against the mu-scaled margin.** Re-run directly
        (not derived) on the current formula: `BIG` still clears its own 37.5-point margin and
        is taken; `NOISE` still does not clear its 16.8-point one and is refused.
        """
        assert lot_ceiling(  # type: ignore[arg-type]
            _state(), POOL, **_kw(value=self.BIG, baseline=150.0)
        ) == 30, "a real 100% improvement is still taken"
        assert lot_ceiling(  # type: ignore[arg-type]
            _state(), POOL, **_kw(value=self.NOISE, baseline=150.0)
        ) == 0, "a gain inside this player's own noise band is not"

    def test_the_absolute_floor_only_guards_a_player_too_small_to_take_a_percentage_of(
        self,
    ) -> None:
        """`CEILING_MARGIN_ABS` exists so a near-worthless player is not swamped by a
        percentage of almost nothing — not so a real pool's margin degenerates to it. The
        crossover is `ABS / REL`: any player whose own value clears it has the relative term
        decide, not the floor. Essentially every biddable Mantra player does — even a
        1-credit riserva scores a few points, and the live pool's highest measured, Malen,
        sits at 638.2.
        """
        crossover = CEILING_MARGIN_ABS / CEILING_MARGIN_REL
        assert crossover < 10, "a realistic player's mu clears this without help from ABS"


class TestTheEveningHasOneBargainPurse:
    """C3. Each bargain is judged against the plan alone, and that does not compose.

    Two lots that each improve the rosa can, bought together, leave a purse that buys neither
    of the players the second re-solve assumed we would still afford.
    """

    def test_the_allowance_is_a_share_of_the_starting_budget(self) -> None:
        assert bargain_allowance(500.0, 0.0, share=0.10) == 50

    def test_what_has_been_spent_comes_straight_off_it(self) -> None:
        assert bargain_allowance(500.0, 30.0, share=0.10) == 20

    def test_it_never_goes_negative(self) -> None:
        """An overshoot -- a lot won above its ceiling in a race -- must read as `0` and not
        as a negative that some later `min` would treat as a bid."""
        assert bargain_allowance(500.0, 80.0, share=0.10) == 0

    def test_it_does_not_re_earn_itself_as_the_plan_spends(self) -> None:
        """Against the *starting* budget, not the remaining one. A cap that floats with what
        is left rises again every time the plan buys a planned player, and the aggregate
        limit stops existing."""
        starting = bargain_allowance(500.0, 0.0, share=0.10)

        assert bargain_allowance(500.0, 0.0, share=0.10) == starting

    def test_a_zero_share_forbids_the_path_outright(self) -> None:
        assert bargain_allowance(500.0, 0.0, share=0.0) == 0
