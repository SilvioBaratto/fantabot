"""`asta_bench.replay` against the real 2026-09-01 evening. Socket-free, fixture-driven.

Proves SPEC §8 items 2 and 3 the way a unit test on synthetic fixtures cannot: that the whole
room — the ledger fold, the plan solve, `lot_ceiling`, `decide_bid` — agrees on the real
evening's three problem lots, using the same golden pool (`tests/_golden.py`) every other
golden test pins.

Numbers below are measured, not copied from `SPEC.md`'s narrative — its "Vicario taken at ≤ 9"
read an intermediate rung as the clearing price; the real, final price was 58, and §8 was
corrected to match this file's own measurement (see `SPEC.md` §8 items 2 and 3).
"""

from __future__ import annotations

import json
from datetime import date

from _golden import load_clearing_sales, load_listone_bridge, load_quotazioni, load_sentiment
from _paths import GOLDEN

from fantabot.application.asta_bench import BenchScenario, Rung, replay
from fantabot.application.plan_inputs import PlanInputs, build_plan_inputs
from fantabot.domain.asta.prices import Sale, mean_prices
from fantabot.domain.asta.sentiment import SentimentWeights

BENCH_FIXTURES = GOLDEN / "asta_2026_09_01"

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


def _scenario(name: str, filename: str, *, uuid_key: str, rung_key: str) -> tuple[BenchScenario, int]:
    """Load one `tests/golden/asta_2026_09_01/*.json` fixture into a `BenchScenario`.

    The three fixtures don't share one shape — Vicario's came from `asta_assignment.ladder`
    (`player_uuid`/`ladder`, real bidder identity per rung); Ostigard's and Malen's came from
    our own `room_journal.jsonl` (`lot_uuid`/`rows`, no bidder identity, since the harvester
    missed their window — see the fixtures' own `_derived_from`). This is the one place that
    difference is absorbed.
    """
    raw = json.loads((BENCH_FIXTURES / filename).read_text(encoding="utf-8"))
    rungs = tuple(
        Rung(at_ms=row["at_ms"], price=row["price"], team_id=row.get("team_id"))
        for row in raw[rung_key]
    )
    purchases = tuple(
        (str(p["fantacalcio_id"]), p["price"]) for p in raw["our_purchases_before"]
    )
    scenario = BenchScenario(
        name=name,
        lot_uuid=raw[uuid_key],
        fantacalcio_id=str(raw["fantacalcio_id"]),
        our_purchases_before=purchases,
        rungs=rungs,
    )
    return scenario, raw["final_price"]


class TestVicarioIsConsideredAndDeclinedAtEveryPrice:
    """SPEC §8 item 2: never a target; §8 item 3: never silently held."""

    def test_every_rung_is_a_considered_pass(self) -> None:
        world, bridge = _world()
        scenario, final_price = _scenario(
            "vicario", "vicario_ladder.json", uuid_key="player_uuid", rung_key="ladder"
        )
        assert final_price == 58

        rows = replay(
            scenario,
            pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
            legality=world.legality, names=world.names, bridge=bridge,
        )

        assert len(rows) == len(scenario.rungs)
        assert all(row["decision"] == "pass" for row in rows)
        assert all(row["walk_away"] == 0 for row in rows)
        assert all(row["provenance"] == "bargain" for row in rows)


class TestOstigardIsGatedForFreeNotSilentlyHeld:
    """A `None` provenance here is the materiality gate working, not defect A recurring —
    his book (15) is below `BARGAIN_MIN_BOOK` (20), so `opportunistic_walkaway` declines
    before any re-solve runs. SPEC §8 item 3 draws exactly this line."""

    def test_every_poll_holds_on_the_free_pre_gate(self) -> None:
        world, bridge = _world()
        scenario, final_price = _scenario(
            "ostigard", "ostigard_journal.json", uuid_key="lot_uuid", rung_key="rows"
        )
        assert final_price == 1

        rows = replay(
            scenario,
            pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
            legality=world.legality, names=world.names, bridge=bridge,
        )

        assert len(rows) == len(scenario.rungs)
        assert all(row["decision"] == "hold" for row in rows)
        assert all(row["provenance"] is None for row in rows)
        assert all(row["walk_away"] is None for row in rows)


class TestMalenIsPricedAboveTheMinimumAndRefusedOnceHePassesTheCeiling:
    """SPEC §8 item 2: ceiling ≥ 40. His real price climbed 0 → 97 across the 19 recorded
    polls; the fixed bidder prices him at a steady 50 throughout (`provenance="bargain"` on
    every row — never the silence the plan gave him before) and would have raised while the
    live price was under that (0, 30) before correctly refusing once it passed 50 — the
    price he actually cleared at, 97, well above."""

    def test_every_poll_prices_him_and_the_decision_tracks_the_ceiling(self) -> None:
        world, bridge = _world()
        scenario, final_price = _scenario(
            "malen", "malen_journal.json", uuid_key="lot_uuid", rung_key="rows"
        )
        assert final_price == 97

        rows = replay(
            scenario,
            pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
            legality=world.legality, names=world.names, bridge=bridge,
        )

        assert len(rows) == len(scenario.rungs)
        assert all(row["provenance"] == "bargain" for row in rows)
        assert all(row["walk_away"] == 50 for row in rows)
        assert all(row["walk_away"] >= 40 for row in rows)
        for row in rows:
            price = row["price"]
            assert isinstance(price, int)
            if price < 50:
                assert row["decision"] == "bid"
            else:
                assert row["decision"] == "pass" and row["reason"] == "walk_away"
