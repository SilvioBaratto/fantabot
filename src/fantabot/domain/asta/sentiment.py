"""Turn a sentiment reading into a multiplier on ``fvm``. Pure: no I/O, no clock.

The value the optimizer prices is ``fvm`` — the market's fantavalore. It is a price, not a
projection, and it prices a nailed-on starter and a talented bench player far closer
together than their fantasy output lands. This module is the correction, in four layers:

1. **The gate** — will he be on the pitch? ``disponibilita`` and ``titolarita``, each
   through its own floor.
2. **The tilt** — how well will he do when he is? ``sentiment``, ``forma``, ``mercato``,
   ``rigorista``, ``piazzati``, as a small multiplicative correction on the gate.
3. **The confidence shrink** — how much does the model actually know? A reading with no
   evidence behind it must not move anything.
4. **The normalization** — the effect is renormalized so its pool mean is exactly 1.0.

Gate and tilt are kept separate rather than blended into one weighted sum because the
fields mean different things: availability is a *probability*, and forma/mercato/rigorista
are *quality* adjustments. ``k`` is small on purpose — the tilt corrects the gate, it does
not overrule it, and a benched player with a glowing write-up must not outrank a starter
with a dull one.

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
because ``news fetch`` is weekly, so one half-life is exactly one missed run. There is no
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

    #: The tilt. Five weights summing to 1.0, and ``k`` scaling the whole term. None of
    #: them is fitted — there is one ``data_run`` — so they are declared priors and
    #: ``k = 0`` is an exact revert to the gate alone, pinned by a test.
    k: float = 0.25
    #: How much a stale Mantra role tag widens the band. Role-risk is uncertainty about
    #: where a player's points come from, not a claim that there will be fewer of them —
    #: so it moves the variance and never the mean.
    drift_widening: float = 0.5
    w_sentiment: float = 0.40
    w_forma: float = 0.20
    w_mercato: float = 0.20
    w_rigorista: float = 0.15
    w_piazzati: float = 0.05

    def __post_init__(self) -> None:
        """Refuse values that break the algebra, at construction rather than mid-asta.

        ``k`` is the one an operator actually turns, and it is the one that bites. Past
        ``k * |worst tilt| >= 1`` the quality term goes non-positive, which reinstates the
        exact hard veto ``disp_floor`` exists to remove — and beyond it a *lower* gate
        yields a *higher* value, because a negative quality flips the product's ordering.
        Better a refused flag at startup than a negative valuation at 21:00.

        The ceiling is derived from the weights, not written down: only ``sentiment``,
        ``forma`` and ``mercato`` range into the negatives (``rigorista`` and ``piazzati``
        are ``[0, 1]``), so those three alone set how far down the tilt can pull. A future
        re-weighting moves the bound with them instead of silently invalidating a literal.
        """
        for name in ("tit_floor", "disp_floor"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} is a floor on a [0,1] signal; got {value}")
        if self.half_life_days <= 0:
            raise ValueError(f"half_life_days must be positive; got {self.half_life_days}")
        if self.drift_widening < 0:
            raise ValueError(
                f"drift_widening may only widen a band; got {self.drift_widening}"
            )
        if self.k < 0:
            raise ValueError(f"a negative k inverts the tilt, so bad news would raise a "
                             f"player's value; got {self.k}")

        worst_tilt = self.w_sentiment + self.w_forma + self.w_mercato
        if worst_tilt and self.k * worst_tilt >= 1.0:
            raise ValueError(
                f"k={self.k} makes the quality term non-positive at the worst tilt "
                f"(-{worst_tilt:.2f}), which would let the tilt overrule the gate; "
                f"k must be below {1.0 / worst_tilt:.2f}"
            )


def _age_days(data_run: str, as_of: date) -> float:
    """Whole days between a reading and the day we are valuing on; never negative.

    A future ``data_run`` — clock skew, a machine in another timezone — clamps to 0 rather
    than producing a decay factor above 1. Age may reduce trust; it must never manufacture
    it.
    """
    return float(max(0, (as_of - date.fromisoformat(data_run)).days))


def aged_confidence(
    row: SentimentRow,
    *,
    as_of: date,
    weights: SentimentWeights = SentimentWeights(),
) -> float:
    """How much this reading is worth trusting: the model's own confidence, decayed by age.

    Shared by the mean and the variance on purpose. A stale reading is less informative in
    both directions, and letting the two disagree about how much they trust the same row
    would be a bug nobody would notice until an auction.
    """
    decay: float = 0.5 ** (_age_days(row.data_run, as_of) / weights.half_life_days)
    return row.confidenza * decay


def variance_by_id(
    rows: Mapping[str, SentimentRow],
    pool_ids: Iterable[str],
    *,
    as_of: date,
    base: float,
    widest: float,
    weights: SentimentWeights = SentimentWeights(),
) -> dict[str, float]:
    """Per-player band, interpolated from ``base`` at full trust to ``widest`` at none.

    Variance used to be flat, which made ``lam`` nearly inert: a risk penalty identical for
    every candidate cannot change which candidate wins, so the objective quietly degenerated
    to maximizing the mean. ``confidenza`` is the honest per-player uncertainty and this is
    where it belongs.

    The endpoints are chosen rather than tuned. ``widest`` is the caller's
    ``no_history_variance`` — the band for a player the market never priced — because a
    reading with no evidence behind it tells us exactly as little as never having been
    priced. Saying so by interpolation means there is no magic multiplier to justify, and
    the relationship survives any future change to either endpoint.

    A stale Mantra role tag widens the band further. The platform freezes role tags in late
    July and enforces its own at lineup submission, so a player tagged ``A`` who is actually
    played as ``W`` will still be *fielded* as an ``A`` — but his output profile is a
    winger's, not a centre-forward's. That is uncertainty about where his points come from,
    which is variance. It is emphatically **not** permission to field him as a ``W``; see
    ``legality.py``, which reads ``quotazioni`` and only ``quotazioni``.
    """
    bands: dict[str, float] = {}
    for player_id in pool_ids:
        row = rows.get(player_id)
        if row is None:
            bands[player_id] = widest
            continue
        ignorance = 1.0 - aged_confidence(row, as_of=as_of, weights=weights)
        bands[player_id] = (base + (widest - base) * ignorance) * (
            1.0 + weights.drift_widening * row.deriva_ruolo
        )
    return bands


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

    tilt = (
        weights.w_sentiment * row.sentiment
        + weights.w_forma * row.forma
        + weights.w_mercato * row.mercato
        + weights.w_rigorista * row.rigorista
        + weights.w_piazzati * row.piazzati
    )
    quality = 1.0 + weights.k * tilt

    confidence = aged_confidence(row, as_of=as_of, weights=weights)

    return NEUTRAL + confidence * (gate * quality - NEUTRAL)


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

    A player missing from ``rows``, or carrying ``confidenza == 0``, comes out at exactly
    ``NEUTRAL`` — "no coverage was found" is not "this player is average", and it must not
    move his value in either direction.

    **He is held out of the mean, not merely set to 1.0 before it.** That was the original
    bug and it inverted the feature. Covered players' raw values average well below 1.0,
    because most of a listone is not nailed-on starters; folding neutrals into that mean and
    then dividing them by it multiplied them *up*. On the real 2026-08-28 pool the one
    silent row came out at x1.368 — a 37% premium for having no evidence, and the same
    premium for any listone id the feed had not reached yet. The suite could not see it:
    every test guarding the identity used a pool whose raw values already averaged 1.0,
    which makes the division a no-op.

    Holding them out keeps **both** invariants at once. The covered subset is centred on
    1.0, and since each held-out player contributes exactly 1.0, the mean over the whole
    pool is still exactly 1.0 — so ``lam`` is protected as before.
    """
    ids = list(pool_ids)
    if not ids:
        return {}

    covered = {
        player_id: raw_effect(rows[player_id], as_of=as_of, weights=weights)
        for player_id in ids
        if player_id in rows and rows[player_id].confidenza > 0
    }
    if not covered:
        return {player_id: NEUTRAL for player_id in ids}

    mean = sum(covered.values()) / len(covered)
    return {
        player_id: covered[player_id] / mean if player_id in covered else NEUTRAL
        for player_id in ids
    }
