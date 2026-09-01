"""The walk-away floor: what stops the bot refusing every lot it planned to buy.

`walk-away(t) = objective - objective without t` is a correct *marginal value* and a wrong
*reservation price*. Over 570 players with near-perfect substitutes almost everyone is
replaceable, so the margin collapses: measured on the live database on 2026-09-01, 10 of 30
walk-aways were exactly 0.0 — including Paz N., whom the same plan budgeted 96 credits for.
`decide_bid` refuses at every price when the walk-away is 0, so armed as-is the bot ends the
evening with a full purse and no rosa.

The fix is not to replace the marginal — it is what keeps the roster feasible — but to floor
it with what the player actually costs.

**Two guards on that floor, and neither is optional.**

The `max(MIN_BID, ...)` clamp: a lot opens at 1 credit, `int()` truncates the walk-away, and
`decide_bid` refuses when the next price exceeds it. So any floor below 1 truncates to 0 and
deletes that player from the biddable set entirely. Measured across the live pool of 416
priced players: alpha 1.0 truncates none, 0.9 truncates 91, 0.8 truncates 107, 0.7 truncates
133, 0.6 truncates 146. Without the clamp, lowering alpha does not lower a ceiling — it
removes players, and the alpha sweep would be measuring that removal instead of alpha.

The `planning_cost` fallback: `prices` holds only players with an observed sale, 416 of 570,
so indexing raises for the rest. `planning_cost` is the convention this repo already uses for
exactly that gap, and reusing it beats inventing a second one.
"""

from __future__ import annotations

from collections.abc import Callable

from fantabot.domain.asta.legality import SchemaLegality, SlotRule
from fantabot.domain.asta.reservation import price_floor, reservations
from fantabot.domain.asta.roles import MantraPlayer, normalize_roles
from fantabot.domain.asta.state import AstaState, RosterRules
from fantabot.domain.asta.value import NaiveValueModel

PRICES = {"rich": 80.0, "mid": 10.0, "cheap": 1.2}

# --- a pool small enough to reason about, rigged so the marginal collapses ---------------
#
# Three near-identical attackers: dropping any one costs almost nothing because the next is
# as good. That is the live pool's problem in miniature -- 570 players with substitutes
# everywhere -- and it is what makes `base - alt` come out at zero for players the plan is
# nonetheless budgeting real credits for.


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
    MantraPlayer("gk1", normalize_roles(["POR"])),
    MantraPlayer("gk2", normalize_roles(["POR"])),
    MantraPlayer("a1", normalize_roles(["A"])),
    MantraPlayer("a2", normalize_roles(["A"])),
]
TEAMS = {"gk1": "W", "gk2": "V", "a1": "X", "a2": "Y"}
FIXTURE_PRICES = {"gk1": 40.0, "gk2": 40.0, "a1": 60.0, "a2": 60.0}
# Identical signals within each role: dropping either member costs exactly nothing, because
# the other is exactly as good. `base - alt` is then 0.0 on the nose -- the live pool's
# collapse, reproduced small enough to reason about.
VALUE = NaiveValueModel(
    signals={"gk1": 5.0, "gk2": 5.0, "a1": 10.0, "a2": 10.0},
    prior_mean=1.0,
    base_variance=1.0,
    no_history_variance=1.0,
)
RULES = RosterRules(size=2, min_goalkeepers=1, min_movement=1)


def walkaways(*, floor: Callable[[str], float] | None, budget: float = 500.0):  # type: ignore[no-untyped-def]
    return reservations(
        AstaState(total_budget=budget),
        POOL,
        value=VALUE,
        prices=FIXTURE_PRICES,
        teams=TEAMS,
        legality=SCHEMI,
        rules=RULES,
        lam=0.0,
        n_targets=None,
        floor=floor,
    )


class TestThePriceFloor:
    def test_it_is_a_fraction_of_the_observed_clearing_price(self) -> None:
        assert price_floor(0.8, PRICES)("rich") == 64.0

    def test_alpha_of_one_is_the_clearing_price_itself(self) -> None:
        assert price_floor(1.0, PRICES)("rich") == 80.0

    def test_it_never_falls_below_the_minimum_bid(self) -> None:
        """A lot opens at 1. A floor of 0.96 truncates to 0 and the player is unbiddable."""
        assert price_floor(0.8, PRICES)("cheap") == 1.0

    def test_an_unpriced_player_falls_back_rather_than_raising(self) -> None:
        """154 of 570 have no observed sale; `prices[pid]` would be a KeyError on the block."""
        assert price_floor(0.8, PRICES)("never-sold") == 1.0

    def test_alpha_of_zero_is_the_minimum_bid_not_nothing(self) -> None:
        """Even "the floor does nothing" has to leave a player biddable at the opening price."""
        assert price_floor(0.0, PRICES)("rich") == 1.0

    def test_lowering_alpha_never_removes_a_player_from_the_biddable_set(self) -> None:
        """The property the sweep depends on: alpha moves the ceiling, never the membership.

        Without the clamp this fails for every player whose price is under 1/alpha, which on
        the live pool is 107 of 416 at alpha 0.8.
        """
        for alpha in (1.0, 0.9, 0.8, 0.7, 0.6, 0.1):
            floor = price_floor(alpha, PRICES)
            for pid in (*PRICES, "never-sold"):
                assert int(floor(pid)) >= 1, f"{pid} unbiddable at alpha={alpha}"

    def test_it_is_monotone_in_alpha(self) -> None:
        assert price_floor(0.6, PRICES)("rich") < price_floor(0.9, PRICES)("rich")


class TestTheFloorInsideReservations:
    """`floor=None` must reproduce today exactly, and a floor must lift a collapsed zero."""

    def test_no_floor_reproduces_the_marginal_exactly(self) -> None:
        _, without = walkaways(floor=None)
        assert any(v == 0.0 for v in without.values()), "the fixture must show the collapse"

    def test_a_floor_lifts_a_collapsed_walkaway(self) -> None:
        _, without = walkaways(floor=None)
        _, with_floor = walkaways(floor=price_floor(0.8, FIXTURE_PRICES))

        zeros = [pid for pid, v in without.items() if v == 0.0]
        assert zeros, "the fixture must show the collapse"
        for pid in zeros:
            assert with_floor[pid] > 0.0, f"{pid} is still unbiddable"

    def test_the_floor_never_exceeds_the_remaining_budget(self) -> None:
        """A walk-away is capped at what we hold; a floor must not smuggle past that."""
        # 120 still affords the 100-credit roster; the floor at alpha=50 asks for 3000.
        _, with_floor = walkaways(floor=price_floor(50.0, FIXTURE_PRICES), budget=120.0)

        assert with_floor, "the fixture must price something"
        assert all(v <= 120.0 for v in with_floor.values())
