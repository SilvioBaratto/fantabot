"""`asta_bench.replay` against the real 2026-09-01 evening. Socket-free, fixture-driven.

Proves SPEC §8 items 2 and 3 the way a unit test on synthetic fixtures cannot: that the whole
room — the ledger fold, the plan solve, `lot_ceiling`, `decide_bid` — agrees on the real
evening's three problem lots, using the same golden pool (`tests/_golden.py`) every other
golden test pins.

Numbers below are measured, not copied from `SPEC.md`'s narrative — its "Vicario taken at ≤ 9"
read an intermediate rung as the clearing price; the real, final price was 58, and §8 was
corrected to match this file's own measurement (see the parity phase's spec, archived and not
in this checkout, §8 items 2 and 3).
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from _golden import load_clearing_sales, load_listone_bridge, load_quotazioni, load_sentiment
from _paths import GOLDEN

from fantabot.application.asta_bench import (
    BENCH_CHECKS,
    BENCH_SCENARIOS,
    bench_checks,
    load_scenario,
    replay,
)
from fantabot.application.plan_inputs import PlanInputs, build_plan_inputs
from fantabot.domain.asta.prices import Sale, mean_prices
from fantabot.domain.asta.sentiment import SentimentWeights

BENCH_FIXTURES = GOLDEN / "asta_2026_09_01"

#: `name -> (filename, uuid_key, rung_key)`, from the one shared scenario table
#: `interface/asta.py`'s `asta bench` command also drives itself from.
_SCENARIOS = {name: (filename, uuid_key, rung_key) for name, filename, uuid_key, rung_key in BENCH_SCENARIOS}

#: The captured `player_sentiment` run these fixtures share with the rest of `tests/golden/`.
PINNED_TODAY = date(2026, 8, 28)


def _world() -> tuple[PlanInputs, dict[str, int]]:
    """The real plan world, narrowed to what FantaLab's listone could call — exactly how
    `asta live`/`asta bid` build it (`interface/asta.py`'s `callable_ids`)."""
    bridge = load_listone_bridge()
    prices = mean_prices(Sale(player_id, price) for player_id, price in load_clearing_sales())
    world = build_plan_inputs(
        load_quotazioni(),
        prices,
        load_sentiment(),
        as_of=PINNED_TODAY,
        tilt_k=SentimentWeights().k,
        callable_ids={str(fid) for fid in bridge.values()},
    )
    return world, bridge


def _final_price(filename: str) -> int:
    """The fixture's own recorded clearing price — asserted separately from `bench_checks`,
    which judges the replay's rows, not the fixture's own metadata."""
    raw = json.loads((BENCH_FIXTURES / filename).read_text(encoding="utf-8"))
    return int(raw["final_price"])


class TestVicarioIsConsideredAndDeclinedAtEveryPrice:
    """SPEC §8 item 2: never a target; §8 item 3: never silently held.

    The invariant itself lives in `asta_bench.bench_checks` — the same function
    `interface/asta.py`'s `asta bench` command checks — so this test and that command cannot
    quietly disagree about what "Vicario passes" means.
    """

    def test_every_rung_is_a_considered_pass(self) -> None:
        world, bridge = _world()
        filename, uuid_key, rung_key = _SCENARIOS["Vicario"]
        scenario = load_scenario(BENCH_FIXTURES, "Vicario", filename, uuid_key=uuid_key, rung_key=rung_key)
        assert _final_price(filename) == 58

        rows = replay(
            scenario,
            pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
            legality=world.legality, names=world.names, bridge=bridge,
        )

        assert len(rows) == len(scenario.rungs)
        assert bench_checks("Vicario", rows) == []


class TestOstigardIsGatedForFreeNotSilentlyHeld:
    """A `None` provenance here is the materiality gate working, not defect A recurring —
    his book (15) is below `BARGAIN_MIN_BOOK` (20), so `opportunistic_walkaway` declines
    before any re-solve runs. SPEC §8 item 3 draws exactly this line. See
    `TestVicarioIsConsideredAndDeclinedAtEveryPrice` for why the check itself is shared."""

    def test_every_poll_holds_on_the_free_pre_gate(self) -> None:
        world, bridge = _world()
        filename, uuid_key, rung_key = _SCENARIOS["Ostigard"]
        scenario = load_scenario(BENCH_FIXTURES, "Ostigard", filename, uuid_key=uuid_key, rung_key=rung_key)
        assert _final_price(filename) == 1

        rows = replay(
            scenario,
            pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
            legality=world.legality, names=world.names, bridge=bridge,
        )

        assert len(rows) == len(scenario.rungs)
        assert bench_checks("Ostigard", rows) == []


class TestMalenIsPricedAboveTheMinimumAndRefusedOnceHePassesTheCeiling:
    """SPEC §8 item 2: ceiling ≥ 40. His real price climbed 0 → 97 across the 19 recorded
    polls; the fixed bidder prices him at a steady 50 throughout (`provenance="bargain"` on
    every row — never the silence the plan gave him before) and would have raised while the
    live price was under that (0, 30) before correctly refusing once it passed 50 — the
    price he actually cleared at, 97, well above. See
    `TestVicarioIsConsideredAndDeclinedAtEveryPrice` for why the check itself is shared."""

    def test_every_poll_prices_him_and_the_decision_tracks_the_ceiling(self) -> None:
        world, bridge = _world()
        filename, uuid_key, rung_key = _SCENARIOS["Malen"]
        scenario = load_scenario(BENCH_FIXTURES, "Malen", filename, uuid_key=uuid_key, rung_key=rung_key)
        assert _final_price(filename) == 97

        rows = replay(
            scenario,
            pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
            legality=world.legality, names=world.names, bridge=bridge,
        )

        assert len(rows) == len(scenario.rungs)
        assert bench_checks("Malen", rows) == []


class TestTheGateHasAnOpinionAboutEveryScenarioItRuns:
    """Audit 1.4: `bench_checks` was an `if`/`elif`/`elif` on three string literals with no
    `else`, so every other name returned `[]` — which is what this function says PASS with.
    All three tests in this file assert `bench_checks(NAME, rows) == []`, so a renamed or a
    newly added scenario made them pass while checking nothing, and made `asta bench` print
    PASS for a lot no invariant had been written for.

    These three close both halves. The first is what makes the other three classes above
    unable to go vacuous: they are green because the table has an entry for the name they
    pass, and the table is pinned to `BENCH_SCENARIOS` with `==` rather than `>=` — a
    dropped check is as red as a missing one (audit 1.6's reason).
    """

    def test_the_scenario_table_and_the_check_table_name_the_same_scenarios(self) -> None:
        replayed = {name for name, _filename, _uuid_key, _rung_key in BENCH_SCENARIOS}

        assert replayed == set(BENCH_CHECKS), (
            "every scenario `asta bench` replays needs invariants, and every set of "
            "invariants needs a scenario that runs it"
        )

    def test_a_scenario_with_no_invariants_is_refused_and_not_answered_with_no_failures(
        self,
    ) -> None:
        """The rename this closes: a real replay's rows, under a name the table lost."""
        rows = [{"decision": "pass", "walk_away": 0, "provenance": "bargain", "price": 1}]

        with pytest.raises(ValueError, match="no bench invariants declared"):
            bench_checks("Vicari", rows)

    @pytest.mark.parametrize("name", sorted(BENCH_CHECKS))
    def test_a_replay_that_journaled_nothing_is_not_a_pass(self, name: str) -> None:
        """Every check is an `all(...)` over `rows` and so is vacuously true over none: a
        `RoomTracker` that silently stopped cycling would have read as three green
        scenarios and an `asta bench` exit code of 0."""
        assert bench_checks(name, []) != []
