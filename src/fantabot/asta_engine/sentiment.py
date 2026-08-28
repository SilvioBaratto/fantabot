"""Turn a sentiment reading into a multiplier on ``fvm``. Pure: no I/O, no clock.

The value the optimizer prices is ``fvm`` — the market's fantavalore. It is a price, not a
projection, and it prices a nailed-on starter and a talented bench player far closer
together than their fantasy output lands. This module is the correction, in three layers:

1. **The gate** — will he be on the pitch? ``disponibilita`` and ``titolarita``, each
   through its own floor.
2. **The confidence shrink** — how much does the model actually know? A reading with no
   evidence behind it must not move anything.
3. **The normalization** — the effect is renormalized so its pool mean is exactly 1.0.

Both floors matter, and for different reasons.

``TIT_FLOOR`` is set from measurement: ``fvm`` and ``titolarita`` share R² approx 0.37-0.43 of
their rank variance, so roughly 40% of what the gate would "discover" is already in the
price. The mapping is deliberately blunt — *the fraction the market already prices is the
fraction of value the gate refuses to strip away.*

``DISP_FLOOR`` exists because the formula without it was wrong. ``disponibilita`` entered
as a bare multiplier, so a reading of 0 forced the gate to 0, the mean to 0, and the player
out of the pool **at any price** — a hard veto wearing a soft weight's clothes. It fires on
real rows: on the 2026-08-28 run Yildiz (``fvm`` 150, metatarsal fracture, three sources,
``confidenza`` 0.95) came out at x0.07. The reading was right; using it that way was not.
``disponibilita`` asks "available *now*"; an asta buys a **season**, and a typical injury
costs a minority of one. So availability gets a floor too, and Yildiz lands at x0.33 —
heavily marked down, still buyable at the right price, which is what an auction is for.

**Why the pool mean is pinned at 1.0.** The optimizer maximizes ``sum(mu) - lam * Var``,
and ``Var`` does not scale with ``mu``. Multiplying every mean by a gate whose pool average
is ~0.7 would quietly make ``lam`` half again as strong — re-tuning the operator's risk knob
behind their back. Normalizing confines this module's effect to *relative* ordering, which
is the only thing it is entitled to change. It also bounds double-counting: the market
already discounts known backups, so a player should move only relative to what the market
already expected of the average player.

**Staleness rides on ``confidenza``** rather than becoming a second concept with its own
threshold — a dated reading is simply a less trustworthy one. The half-life is 7 days
because ``news-fetch`` is weekly, so one half-life is exactly one missed run. There is no
cliff, and at high age the effect converges to 1.0: the honest fallback of plain ``fvm``.

``as_of`` is a parameter, never ``date.today()``. A pure module that reads the clock is a
module whose tests are a coin flip.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from fantabot.data_sources.models import SentimentRow

#: What "no opinion" is worth. A silent row and an absent player both land here.
NEUTRAL = 1.0


@dataclass(frozen=True)
class SentimentWeights:
    """The tunables. Every one is a declared prior; only ``tit_floor`` is fitted.

    ``tit_floor`` is why a squad player keeps value and ``disp_floor`` is why an injured one
    does: both fields ask about the *next* matchday, and an asta is a season-long bet.
    Without ``disp_floor`` an injured player's mean is exactly 0 and he is unbuyable at any
    price — a veto, not a weight.
    """

    tit_floor: float = 0.40
    disp_floor: float = 0.50
    half_life_days: float = 7.0


def _age_days(data_run: str, as_of: date) -> float:
    """Whole days between a reading and the day we are valuing on; never negative.

    A future ``data_run`` — clock skew, a machine in another timezone — clamps to 0 rather
    than producing a decay factor above 1. Age may reduce trust; it must never manufacture
    it.
    """
    return float(max(0, (as_of - date.fromisoformat(data_run)).days))


def raw_effect(
    row: SentimentRow,
    *,
    as_of: date,
    weights: SentimentWeights = SentimentWeights(),
) -> float:
    """One player's multiplier before normalization. ``NEUTRAL`` means "no opinion".

    Deliberately public: the decay and shrink properties are stated about *this* value, and
    normalization is a pool-wide operation that distorts any single player's distance from
    neutral. Asserting "a week-old reading carries half the adjustment" is only meaningful
    here.
    """
    avail = weights.disp_floor + (1.0 - weights.disp_floor) * row.disponibilita
    start = weights.tit_floor + (1.0 - weights.tit_floor) * row.titolarita
    gate = avail * start

    decay: float = 0.5 ** (_age_days(row.data_run, as_of) / weights.half_life_days)
    confidence = row.confidenza * decay

    return NEUTRAL + confidence * (gate - NEUTRAL)


def effect_by_id(
    rows: Mapping[str, SentimentRow],
    pool_ids: Iterable[str],
    *,
    as_of: date,
    weights: SentimentWeights = SentimentWeights(),
) -> dict[str, float]:
    """Per-player multiplier on ``fvm``, renormalized to a pool mean of exactly 1.0.

    ``rows`` is the whole feed (``all_latest`` returns every player ever queried);
    ``pool_ids`` is the listone being valued. Rows outside the pool are ignored rather than
    folded into the mean — the normalization is over the players actually being chosen
    between.

    A player missing from ``rows``, or carrying ``confidenza == 0``, is ``NEUTRAL`` before
    normalization. That is not a special case: ``confidenza == 0`` makes the shrink term
    vanish on its own, which is precisely the invariant ``news_sentiment`` exists to hold —
    "no coverage was found" is not "this player is average".
    """
    ids = list(pool_ids)
    if not ids:
        return {}

    raw = {
        player_id: (
            raw_effect(rows[player_id], as_of=as_of, weights=weights)
            if player_id in rows
            else NEUTRAL
        )
        for player_id in ids
    }
    mean = sum(raw.values()) / len(raw)
    return {player_id: value / mean for player_id, value in raw.items()}
