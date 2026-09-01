"""One `asta bid` cycle stays cheap, and the ceiling is a call count.

**Why this exists.** P10 took a cycle from 71.5 ms and 2,069,696 calls to 17.1 ms and
351,393, and every one of those wins is the kind a later change undoes by accident:
re-deriving the index per solve, looking a value up inside a loop, selecting a mode
per player instead of per slot. None of that breaks a test — the answer is identical,
only slower — so without this the regression would be found during a live asta, which
is the one time nobody is reading profiles.

**Why calls and not seconds.** Wall time depends on the machine, the load and the
interpreter; a threshold loose enough not to flake on a busy laptop is loose enough to
miss a 3x regression. Call count is a property of the code. It is stable to the digit
here, which is what lets the ceiling sit close to the real number instead of an order
of magnitude above it.

**Why one number and not a benchmark.** The point is not to track performance, it is
to notice a *reversal*. A ceiling with honest headroom does that and costs one cycle.
"""

from __future__ import annotations

import cProfile
import io
import pstats

import pytest
from _golden import (
    PINNED_TODAY,
    load_clearing_sales,
    load_quotazioni,
    load_sentiment,
)

#: Measured 351,393 on 2026-08-30, from a baseline of 2,069,696. The ceiling is ~1.4x
#: the measurement: loose enough that an incidental refactor does not flake it, tight
#: enough that undoing any single P10 change fails — the cheapest of them, hoisting the
#: index across the six solves of a cycle, is worth ~76,000 calls on its own.
CEILING = 500_000

#: What a reversal looks like. Kept beside the ceiling so the failure message can say
#: how far back the code has slipped rather than just that a number grew.
BASELINE_BEFORE_P10 = 2_069_696

#: The same guard for the cycle `asta bid` actually runs. It passes `n_targets=None` —
#: every unowned member of the plan, not the top five — because lots are called in
#: arbitrary order and a plan priced five deep holds on the other twenty-five.
#:
#: Measured 1,508,707 on 2026-09-01 against the live pool; the ceiling is ~1.4x that, the
#: same margin `CEILING` uses. Two ceilings rather than one raised one: 1.5M sits *below*
#: `BASELINE_BEFORE_P10`, so a single ceiling wide enough for this cycle could no longer
#: tell "priced the whole plan" from "someone reverted P10". Each number guards one thing.
FULL_PLAN_CEILING = 2_100_000


@pytest.fixture(scope="module")
def world():  # type: ignore[no-untyped-def]
    from fantabot.application.asta_planner import build_plan_inputs
    from fantabot.domain.asta.prices import Sale, mean_prices

    return build_plan_inputs(
        load_quotazioni(),
        mean_prices(Sale(pid, price) for pid, price in load_clearing_sales()),
        load_sentiment(),
        as_of=PINNED_TODAY,
        tilt_k=0.25,
    )


def _cycle_calls(world) -> int:  # type: ignore[no-untyped-def]
    """Total Python calls in one `reservations` cycle over the pinned 548-player pool."""
    from fantabot.domain.asta.reservation import reservations
    from fantabot.domain.asta.state import AstaState

    def cycle() -> None:
        reservations(
            AstaState(total_budget=500.0),
            world.pool,
            value=world.value,
            prices=world.prices,
            teams=world.teams,
            legality=world.legality,
            lam=0.3,
        )

    cycle()  # warm: first call populates the sentiment-derived caches
    profiler = cProfile.Profile()
    profiler.enable()
    cycle()
    profiler.disable()
    return pstats.Stats(profiler, stream=io.StringIO()).total_calls


def _full_plan_cycle_calls(world) -> int:  # type: ignore[no-untyped-def]
    """The same measurement for `n_targets=None` — the cycle the live bidder runs."""
    from fantabot.domain.asta.reservation import reservations
    from fantabot.domain.asta.state import AstaState

    def cycle() -> None:
        reservations(
            AstaState(total_budget=500.0),
            world.pool,
            value=world.value,
            prices=world.prices,
            teams=world.teams,
            legality=world.legality,
            lam=0.3,
            n_targets=None,
        )

    cycle()  # warm, for the same reason as above
    profiler = cProfile.Profile()
    profiler.enable()
    cycle()
    profiler.disable()
    return pstats.Stats(profiler, stream=io.StringIO()).total_calls


def test_one_cycle_stays_under_the_ceiling(world) -> None:  # type: ignore[no-untyped-def]
    calls = _cycle_calls(world)

    assert calls <= CEILING, (
        f"one asta-bid cycle now costs {calls:,} calls, over the {CEILING:,} ceiling. "
        f"P10 brought this from {BASELINE_BEFORE_P10:,} to 351,393; something has "
        "given part of that back. The usual causes, in the order they were fixed: the "
        "per-player index rebuilt per solve instead of once per cycle, a value lookup "
        "moved back inside a loop, or `can_field` selecting its mode per player again. "
        "If the cost is deliberate, raise the ceiling in the same commit and say why."
    )


def test_the_ceiling_is_not_so_loose_that_it_would_miss_a_reversal(world) -> None:
    """A guard that cannot fail is worse than none: it reads as coverage.

    The ceiling has to sit well below what the code cost before P10, or a full
    reversal would pass it.
    """
    assert CEILING < BASELINE_BEFORE_P10 / 3, (
        "the ceiling is within 3x of the pre-P10 cost — it would admit a reversal"
    )
    assert _cycle_calls(world) < CEILING * 0.8, (
        "the measurement is within 20% of the ceiling, so this will flake before it "
        "catches anything; re-measure and re-set both numbers deliberately"
    )


def test_the_full_plan_cycle_stays_under_its_own_ceiling(world) -> None:  # type: ignore[no-untyped-def]
    """`asta bid` prices the whole plan, so the whole plan needs its own budget.

    This is the cycle that runs every two seconds in a live room. Wall clock is not the
    constraint — 77 ms measured, inside a 2 s poll — but an unbounded walk-away loop is
    how that stops being true.
    """
    calls = _full_plan_cycle_calls(world)

    assert calls <= FULL_PLAN_CEILING, (
        f"the full-plan cycle now costs {calls:,} calls, over the {FULL_PLAN_CEILING:,} "
        "ceiling. Each walk-away is one more roster solve, so the usual cause is a target "
        "list that grew past the plan — `n_targets=None` must mean the unowned members of "
        "the optimal roster, never the pool. If the cost is deliberate, raise the ceiling "
        "in the same commit and say why."
    )


def test_the_two_ceilings_measure_different_things(world) -> None:  # type: ignore[no-untyped-def]
    """One raised ceiling would have retired the P10 tripwire.

    The full-plan cycle costs more than the code did before P10 was optimised at all, so a
    single ceiling loose enough to admit it could not distinguish the two. That is the whole
    reason there are two numbers instead of one.
    """
    capped = _cycle_calls(world)
    full = _full_plan_cycle_calls(world)

    assert full > capped, "n_targets=None must price more targets than the default five"
    assert FULL_PLAN_CEILING > BASELINE_BEFORE_P10, (
        "if the full-plan ceiling sat below the pre-P10 baseline the two guards would be "
        "interchangeable, and this test would be asserting nothing"
    )
    assert full < FULL_PLAN_CEILING * 0.8, (
        "the full-plan measurement is within 20% of its ceiling, so it will flake before it "
        "catches anything; re-measure and re-set both numbers deliberately"
    )
