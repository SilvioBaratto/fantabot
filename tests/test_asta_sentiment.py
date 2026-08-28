"""The pure sentiment algebra: gate, confidence shrink, age decay, normalization.

Every decision recorded in ``docs/spec-asta-sentiment.md`` that can be expressed as an
assertion lives here. Three of them are load-bearing rather than descriptive:

* **No effect is ever 0.** Task 1's measurement found the original formula used
  ``disponibilita`` as a bare multiplier, so an injured player's mean went to exactly 0 and
  he became unbuyable at any price. A veto wearing a soft weight's clothes.
* **The silent-row identity.** ``confidenza == 0`` means "no coverage was found", not
  "neutral", so it must leave the value untouched rather than push it anywhere.
* **The normalization property.** The objective is ``sum(mu) - lam * Var`` and ``Var`` does
  not move with ``mu``; rescaling every mean would silently re-tune ``lam``. Pinning the
  pool mean at 1.0 is what keeps the risk knob meaning what it meant.
"""

from __future__ import annotations

import itertools
from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from fantabot.asta_engine.sentiment import NEUTRAL, SentimentWeights, effect_by_id, raw_effect
from fantabot.data_sources.models import SentimentRow

AS_OF = date(2026, 8, 28)


def row(player_id: str = "1", *, run: str = "2026-08-28", **scores: float) -> SentimentRow:
    """A reading with neutral-ish defaults; pass only what the test is about."""
    base: dict[str, float] = {
        "sentiment": 0.0,
        "disponibilita": 1.0,
        "titolarita": 1.0,
        "mercato": 0.0,
        "forma": 0.0,
        "rigorista": 0.0,
        "piazzati": 0.0,
        "confidenza": 1.0,
    }
    base.update(scores)
    return SentimentRow(
        player_id=player_id,
        nome=f"p{player_id}",
        data_run=run,
        ruolo_campo="",
        ruoli_mantra="",
        deriva_ruolo=0.0,
        **base,
    )


# --- the silent-row identity -------------------------------------------------------


def test_a_silent_row_leaves_the_value_untouched() -> None:
    """confidenza == 0 is "no coverage found", not "neutral" — so it must not move anything."""
    rows = {"1": row("1", confidenza=0.0, titolarita=0.0, disponibilita=0.0)}

    assert effect_by_id(rows, ["1"], as_of=AS_OF)["1"] == pytest.approx(1.0)


def test_a_player_absent_from_the_feed_is_untouched_too() -> None:
    assert effect_by_id({}, ["1"], as_of=AS_OF)["1"] == pytest.approx(1.0)


def test_a_silent_row_and_an_absent_player_are_treated_identically() -> None:
    silent = effect_by_id({"1": row("1", confidenza=0.0)}, ["1"], as_of=AS_OF)
    absent = effect_by_id({}, ["1"], as_of=AS_OF)

    assert silent["1"] == pytest.approx(absent["1"])


# --- the normalization property ----------------------------------------------------


def test_the_pool_mean_of_the_effect_is_exactly_one() -> None:
    """What protects ``lam``: Var does not scale with mu, so mu must not be rescaled."""
    rows = {
        str(i): row(str(i), titolarita=t, disponibilita=d, confidenza=c)
        for i, (t, d, c) in enumerate(
            itertools.product((0.0, 0.3, 1.0), (0.0, 0.5, 1.0), (0.2, 1.0))
        )
    }
    effects = effect_by_id(rows, list(rows), as_of=AS_OF)

    assert sum(effects.values()) / len(effects) == pytest.approx(1.0, abs=1e-9)


def test_the_pool_mean_holds_even_when_every_row_is_silent() -> None:
    rows = {str(i): row(str(i), confidenza=0.0) for i in range(5)}
    effects = effect_by_id(rows, list(rows), as_of=AS_OF)

    assert sum(effects.values()) / len(effects) == pytest.approx(1.0, abs=1e-9)


# --- no veto -----------------------------------------------------------------------


def test_no_effect_is_ever_zero_anywhere_in_the_input_space() -> None:
    """Task 1's finding, as a test.

    Exhaustive over the corners rather than over the 548 real rows: the worst case is the
    corner, and a corner test needs no database.
    """
    corners = [
        row(str(i), titolarita=t, disponibilita=d, confidenza=c)
        for i, (t, d, c) in enumerate(itertools.product((0.0, 1.0), repeat=3))
    ]
    rows = {r.player_id: r for r in corners}

    effects = effect_by_id(rows, list(rows), as_of=AS_OF)

    assert min(effects.values()) > 0.0


def test_an_injured_player_is_marked_down_but_stays_buyable() -> None:
    """Yildiz, 2026-08-28: metatarsal fracture, 3 sources, confidenza 0.95."""
    rows = {
        "hurt": row("hurt", disponibilita=0.0, titolarita=0.0, confidenza=0.95),
        "fit": row("fit", disponibilita=1.0, titolarita=0.9, confidenza=0.95),
    }
    effects = effect_by_id(rows, list(rows), as_of=AS_OF)

    assert 0.0 < effects["hurt"] < effects["fit"]


# --- monotonicity ------------------------------------------------------------------


@pytest.mark.parametrize("field", ["titolarita", "disponibilita"])
def test_the_gate_is_monotone_in_each_playing_time_field(field: str) -> None:
    effects = [
        effect_by_id({"1": row("1", **{field: v})}, ["1", "2"], as_of=AS_OF)["1"]
        for v in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]

    assert effects == sorted(effects)
    assert effects[0] < effects[-1]


# --- age decay ---------------------------------------------------------------------


# Asserted on raw_effect, not on effect_by_id: normalization is a pool-wide operation and
# it distorts any single player's distance from neutral, so "half the adjustment" is only a
# meaningful claim before it.


def test_a_reading_one_half_life_old_carries_half_the_adjustment() -> None:
    fresh = row("1", run="2026-08-28", titolarita=0.0, disponibilita=0.0)
    stale = row("1", run="2026-08-21", titolarita=0.0, disponibilita=0.0)

    fresh_pull = NEUTRAL - raw_effect(fresh, as_of=AS_OF)
    stale_pull = NEUTRAL - raw_effect(stale, as_of=AS_OF)

    assert stale_pull == pytest.approx(fresh_pull / 2)


def test_a_very_stale_reading_converges_to_no_adjustment() -> None:
    ancient = row("1", run="2026-02-28", titolarita=0.0, disponibilita=0.0)

    assert raw_effect(ancient, as_of=AS_OF) == pytest.approx(NEUTRAL, abs=1e-6)


def test_a_reading_from_the_future_is_not_amplified() -> None:
    """Clock skew may reduce trust; it must never manufacture it."""
    future = row("1", run="2026-09-04", titolarita=0.0, disponibilita=0.0)
    today = row("1", run="2026-08-28", titolarita=0.0, disponibilita=0.0)

    assert raw_effect(future, as_of=AS_OF) == pytest.approx(raw_effect(today, as_of=AS_OF))


def test_the_decay_is_the_only_thing_age_changes() -> None:
    """A same-day reading gets its confidence applied undiminished."""
    fresh = row("1", run="2026-08-28", titolarita=0.0, disponibilita=0.0, confidenza=1.0)

    # gate = (0.50 + 0.50*0) * (0.40 + 0.60*0) = 0.5 * 0.4 = 0.20
    assert raw_effect(fresh, as_of=AS_OF) == pytest.approx(0.20)


# --- shape -------------------------------------------------------------------------


def test_an_empty_pool_is_an_empty_mapping() -> None:
    assert effect_by_id({}, [], as_of=AS_OF) == {}


def test_rows_outside_the_pool_do_not_affect_it() -> None:
    """all_latest() returns the whole table; the pool is one listone."""
    only = effect_by_id({"1": row("1", titolarita=0.0)}, ["1", "2"], as_of=AS_OF)
    plus = effect_by_id(
        {"1": row("1", titolarita=0.0), "9": row("9", titolarita=0.0)},
        ["1", "2"],
        as_of=AS_OF,
    )

    assert only == pytest.approx(plus)


def test_the_weights_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        SentimentWeights().tit_floor = 0.9  # type: ignore[misc]


# --- the tilt ----------------------------------------------------------------------
#
# The gate answers "will he be on the pitch". The tilt answers "how well will he do when
# he is". They are kept separate because the fields mean different things: availability is
# a probability, forma/mercato/rigorista are quality adjustments, and blending all of them
# into one weighted sum would treat a probability as a score.


def test_tilt_k_zero_reproduces_the_gate_exactly() -> None:
    """The escape hatch, pinned. Five unfitted weights are safe only if this holds."""
    off = SentimentWeights(k=0.0)
    opinionated = row("1", sentiment=1.0, forma=1.0, mercato=1.0, rigorista=1.0, piazzati=1.0)
    neutral = row("1")

    assert raw_effect(opinionated, as_of=AS_OF, weights=off) == pytest.approx(
        raw_effect(neutral, as_of=AS_OF, weights=off)
    )


def test_a_designated_penalty_taker_is_worth_more() -> None:
    taker = row("1", rigorista=0.9)
    nobody = row("2", rigorista=0.0)

    assert raw_effect(taker, as_of=AS_OF) > raw_effect(nobody, as_of=AS_OF)


def test_a_negative_outlook_lowers_the_value() -> None:
    grim = row("1", sentiment=-0.8, forma=-0.6)

    assert raw_effect(grim, as_of=AS_OF) < raw_effect(row("2"), as_of=AS_OF)


def test_the_tilt_is_still_shrunk_by_confidence() -> None:
    """An unevidenced opinion must not move the value, however strong the opinion."""
    loud_but_unevidenced = row("1", sentiment=1.0, rigorista=1.0, confidenza=0.0)

    assert raw_effect(loud_but_unevidenced, as_of=AS_OF) == pytest.approx(NEUTRAL)


def test_the_tilt_cannot_drive_the_effect_to_zero() -> None:
    """Task 1's rule survives the tilt: worst case in the whole space is still positive."""
    worst = row(
        "1",
        titolarita=0.0,
        disponibilita=0.0,
        sentiment=-1.0,
        forma=-1.0,
        mercato=-1.0,
        rigorista=0.0,
        piazzati=0.0,
        confidenza=1.0,
    )

    assert raw_effect(worst, as_of=AS_OF) > 0.0


def test_the_pool_mean_still_holds_with_the_tilt_live() -> None:
    rows = {
        str(i): row(str(i), titolarita=t, sentiment=s, rigorista=r)
        for i, (t, s, r) in enumerate(
            itertools.product((0.0, 1.0), (-1.0, 0.0, 1.0), (0.0, 1.0))
        )
    }
    effects = effect_by_id(rows, list(rows), as_of=AS_OF)

    assert sum(effects.values()) / len(effects) == pytest.approx(1.0, abs=1e-9)


def test_the_gate_still_dominates_the_tilt() -> None:
    """k is small on purpose: the tilt corrects the gate, it does not overrule it.

    A benched player with a glowing write-up must not outrank a starter with a dull one.
    """
    benched_but_loved = row("1", titolarita=0.0, sentiment=1.0, forma=1.0, rigorista=1.0)
    starter_but_dull = row("2", titolarita=1.0, sentiment=0.0, forma=0.0, rigorista=0.0)

    assert raw_effect(benched_but_loved, as_of=AS_OF) < raw_effect(starter_but_dull, as_of=AS_OF)
