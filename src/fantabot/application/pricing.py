"""Sketch of a target_price function for the 2026/27 asta iniziale, classic or mantra.

This is a research script; `fantabot db price` will be its home once W3 ports it.
It used to say it mirrored `StatsSource.target_price()` — that Protocol was a
guess about a shape nothing implemented and was deleted on 2026-08-30 with the
Classic lineup scaffolding.

**On the citations below.** The four qi-bias analyses they name
(`join_qi_bias_performance.py`, `analyze_qi_bias_by_team.py`,
`analyze_low_minutes_bias.py`, `analyze_qi_bias.py`) were deleted the same day,
their job done. They are kept as provenance, not as paths to follow: every number
they produced that this model depends on is quoted inline below, which is what
makes the constants defensible without them. `git log` has the scripts.

Starts from QI(2026/27) (== QA(2026/27), season hasn't started — confirmed
earlier) and applies two adjustments, both backed by those analyses'
output on 2022/23-2025/26:

1. Regression-to-mean fade (outfield roles only — goalkeepers showed ~0
   correlation, left untouched). Fits pct_delta ~ prior_media_fantavoto per
   role via OLS on the 2023/24-2025/26 has-prior-data cohort (qi>2, floor
   guard), then applies the fitted line to each 2026/27 player's 2025/26
   media_fantavoto. This is a real but modest effect (r ~= -0.19 to -0.25,
   R^2 ~= 4-6%) — most of the adjustment will be small. Slope is clamped to
   the training data's 5th-95th percentile pct_delta range per role so a
   player with an extreme prior fantamedia (outside anything actually
   observed) doesn't get an absurd extrapolated adjustment.

   CRITICAL SCOPE LIMIT, added after catching it live on Malen (18 apps in
   2025/26, media_fantavoto 9.0 from a 14-goal hot stretch): the -0.19/-0.25
   correlation was only ever validated for players with a "regular" prior
   season (25-38 appearances) — analyze_low_minutes_bias.py showed the same
   correlation is only -0.007 to -0.073 for thinner samples. The training
   cohort here is filtered to partite_giocate 25-38 accordingly, and the
   fade is only applied to 2026/27 players whose 2025/26 appearance count
   is also in that range — anyone with a thinner prior season is flagged
   "thin_prior_sample_no_fade" instead of getting an unvalidated extrapolation.

2. NAP/MIL reputation discount. The only two teams whose overpricing held up
   on BOTH mean and median with decent sample size and season-consistency
   (analyze_qi_bias_by_team.py: NAP mean-7.6%/median-15.0%, MIL
   mean-5.7%/median-10.0%, n~94-101, 4 seasons, 75% consistency). Every
   other team's signal either collapsed to median~0% (outlier-driven, see
   that script's output) or came from a 1-2 season sample too thin to trust
   — deliberately NOT generalized into a per-team table. Recomputed here
   from the qi_bias table rather than hardcoded, so it stays in sync if
   the underlying data changes.

Explicitly NOT applied — the low-minutes-good-rate idea was tested in
scripts/analyze_low_minutes_bias.py and found no basis: correlation was
~0 for thin-sample players and the one promising-looking cell was
outlier-driven (mean +74.5%, median -14.3%, n=10). No adjustment for
players with no 2025/26 data either — that cohort has the highest variance
of any group (+65% mean pct_delta) but no *directional* evidence, just
noise dominated by the qi<=1 floor; baking in a markup there would be
inventing a signal that was never actually validated. Flagged as
"no_prior_data" in the output instead so the bidding logic can treat it
as high-uncertainty rather than silently trusting a fabricated adjustment.

NAP/MIL x fade interaction — tested, not just flagged: computed each
fade-eligible NAP/MIL player's residual (actual pct_delta minus what the
fitted fade line already predicts from their prior_media_fantavoto) and
compared to the raw team bias. NAP: raw median -21.2% vs residual -20.1%
(barely moves). MIL: raw median -10.4% vs residual -12.4% (if anything
grows). Conclusion: the team effect is close to independent of the fade —
NAP/MIL players' prior fantamedia isn't elevated enough for the fade to
be silently re-explaining their overpricing. Stacking both multiplicatively,
as done below, is empirically fine, not double-counting. (Side note: even
the non-NAP/MIL cohort shows a -6.9% median residual despite a 0% raw
median — the fade line, fit by OLS on means, runs a bit optimistic
relative to median outcomes generally; a minor calibration gap, not
specific to these two teams, not chased further here.)

Mantra role bucketing: Mantra's ~30 role codes in quotazioni (listone
'mantra') are mostly compound (e.g. "B;DD;DS", "W;T;A") and mostly tiny — the exact-code
per-role breakdown in join_qi_bias_performance.py's mantra output has n<20
for most codes, far below what an OLS fit can trust. Fitting the fade per
exact code the way classic's 4 clean roles allow isn't viable. Instead each
player is assigned ONE macro-role bucket, taken from the FIRST component of
their compound code (assumed primary — the compound string's own ordering
isn't otherwise documented) and mapped by attacking-output proximity, since
that's what actually drives the fantamedia baseline a role-split needs to
control for:
    POR -> GK
    DC, B, DD, DS -> DEF      (center-backs, wing-backs — low-scoring baseline)
    M, C, E       -> MID      (mediano, central mid, esterno)
    W, T          -> MID_ATT  (winger, trequartista — higher attacking involvement)
    A, PC         -> ATT      (out-and-out forwards)
M (mediano) was first tried folded into DEF on the reasoning that a
destroyer-type mid's scoring baseline is closer to a defender's — wrong in
practice: checked every compound code M actually appears in
(quotazioni, listone 'mantra', 2026/27) and it's only ever "M", "M;C", or "E;M",
never paired with a defensive code. "M;C" alone is 66/259 compound-role
players (Lobotka, Locatelli, Modric, Calhanoglu, Rovella...) — a real mix
of destroyers and advanced playmakers, but never defenders. M now folds
into MID instead, alongside C and E.

Usage:
    python scripts/target_price.py [--system classic|mantra] [--data-dir data] [--out ...]
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fantabot.adapters.persistence import scraping as _db
from fantabot.domain.shared.values import BiasRow, PlayerQuote, PriorStats

TRAIN_SEASONS = ["2023/24", "2024/25", "2025/26"]
PREV_OF_TRAIN = dict(zip(TRAIN_SEASONS, ["2022/23", "2023/24", "2024/25"], strict=True))
TARGET_SEASON = "2026/27"
PRIOR_SEASON_FOR_TARGET = "2025/26"

MIN_QI = 2  # floor-effect guard. Measured 2026 by the qi-bias analyses (since deleted):
            # below QI 2 the percentage delta is dominated by the divisor, not by the market.
GOALKEEPER_MACRO = "GK"

# classic: single-letter code, used as-is. mantra: compound code, first component mapped below.
MANTRA_ROLE_TO_MACRO = {
    "POR": GOALKEEPER_MACRO,
    "DC": "DEF", "B": "DEF", "DD": "DEF", "DS": "DEF",
    "M": "MID", "C": "MID", "E": "MID",
    "W": "MID_ATT", "T": "MID_ATT",
    "A": "ATT", "PC": "ATT",
}
CLASSIC_ROLE_TO_MACRO = {"p": GOALKEEPER_MACRO, "d": "DEF", "c": "MID", "a": "ATT"}


def macro_role(role_code: str, system: str) -> str:
    if system == "classic":
        return CLASSIC_ROLE_TO_MACRO[role_code]
    primary = role_code.split(";")[0]
    return MANTRA_ROLE_TO_MACRO[primary]

# regression-to-mean fade only validated for this appearance range (analyze_low_minutes_bias.py:
# correlation -0.191 here vs -0.007 to -0.073 for thinner samples) — both training and application
# are restricted to it
REGULAR_APPEARANCES_LO = 25
REGULAR_APPEARANCES_HI = 38

# validated in analyze_qi_bias_by_team.py — do not extend to other teams without re-checking
# median (not just mean) holds up, per that script's outlier-driven flag
TEAM_DISCOUNT_ALLOWLIST = {"NAP", "MIL"}


@dataclass(frozen=True)
class RoleFade:
    """Fits log(qa/qi) ~ prior_media_fantavoto, not raw pct_delta.

    pct_delta = (qa-qi)/qi is structurally asymmetric — capped near -100% on
    the downside but unbounded on the upside (a cheap player doubling reads
    as +100%, halving only reads as -50%) — which lets a handful of cheap
    breakout players (qi=3-7, pct_delta up to +350%) dominate an OLS fit on
    the raw percentage. log(qa/qi) is symmetric (doubling=+0.69,
    halving=-0.69) and is the mathematically correct scale for a
    multiplicative model like target = qi * adjustment_factor anyway.
    Caught on the MID_ATT (mantra) bucket: mean pct_delta +24.1% vs median
    -7.1%, a gap that size only happens when a few extreme points are
    dragging the mean — Weah (qi=4->qa=18, +350%), De Ketelaere
    (qi=7->qa=26, +271%), Saelemaekers (qi=5->qa=22, +340%) among them, all
    well above the qi>2 floor guard, so this isn't the floor artifact
    already handled elsewhere — it's the pct-scale itself being outlier-prone.
    """

    slope: float
    intercept: float
    clamp_lo: float
    clamp_hi: float

    def predict_log_ratio(self, prior_fantamedia: float) -> float:
        raw = self.slope * prior_fantamedia + self.intercept
        return max(self.clamp_lo, min(self.clamp_hi, raw))

    def predict_factor(self, prior_fantamedia: float) -> float:
        return math.exp(self.predict_log_ratio(prior_fantamedia))


def training_pairs(
    bias_rows: Sequence[BiasRow],
    prior_stats: Mapping[tuple[str, str], PriorStats],
    system: str,
) -> dict[str, list[tuple[float, float]]]:
    """`(prior fantamedia, log(qa/qi))` per macro role -- what a fade is fitted on. Pure.

    The single definition of which observations qualify. It was inline in `fit_fades`,
    and `run` kept a second, looser copy to label the `n` column of its table: that copy
    applied the appearance filter and neither of the other two, so a player written down
    to `qa == 0` was counted beside a slope he had not contributed to.

    Three kinds of row are refused:
    """
    by_role: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in bias_rows:
        role = macro_role(row.role, system)

        # Goalkeepers do not fade with fantamedia the way outfielders do, and are priced
        # at qi by the fallback path.
        if role == GOALKEEPER_MACRO:
            continue

        # `log_ratio` is log(qa/qi) and log(0) is undefined, so this would raise
        # `ValueError: math domain error` from inside a property.
        #
        # It is not merely a crash guard. A player the platform has marked worthless
        # mid-season is a data artefact, not evidence about how quotazioni fade, and the
        # appearance filter below cannot catch him: he is excluded on *this* season's qa
        # while that filter reads his *prior* season. Goglichidze (6537, UDI) is the live
        # case -- qa 0 in 2025/26 with 33 appearances in 2024/25, squarely inside the
        # 25-38 cohort. He still gets priced by the fallback path; he just does not teach
        # the fade.
        if row.qa <= 0:
            continue

        # The regression-to-mean correlation was measured at -0.191 across 25-38
        # appearances and between -0.007 and -0.073 for thinner samples. Outside the
        # range there is no signal to fit.
        prior = prior_stats.get((row.id, PREV_OF_TRAIN[row.stagione]))
        if prior is None or not (
            REGULAR_APPEARANCES_LO <= prior.partite_giocate <= REGULAR_APPEARANCES_HI
        ):
            continue

        by_role[role].append((prior.media_fantavoto, row.log_ratio))
    return by_role


def count_observations(
    bias_rows: Sequence[BiasRow],
    prior_stats: Mapping[tuple[str, str], PriorStats],
    system: str,
) -> dict[str, int]:
    """How many observations each role's fade was fitted from. Pure.

    Derived from `training_pairs`, so it cannot disagree with the fit -- which the
    version it replaces did.
    """
    return {role: len(pairs) for role, pairs in training_pairs(bias_rows, prior_stats, system).items()}


#: Below this a slope is noise, and a noisy slope moves real credits.
MIN_OBSERVATIONS = 20


def fit_fades(
    bias_rows: Sequence[BiasRow],
    prior_stats: Mapping[tuple[str, str], PriorStats],
    system: str,
) -> dict[str, RoleFade]:
    """One fade per macro role, fitted on the observations that qualify. Pure."""
    fades: dict[str, RoleFade] = {}
    for role, pairs in training_pairs(bias_rows, prior_stats, system).items():
        if len(pairs) < MIN_OBSERVATIONS:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        slope, intercept = statistics.linear_regression(xs, ys)
        sorted_ys = sorted(ys)
        # The 5th and 95th percentile of the observed ratios: a clamp keeps one breakout
        # season from repricing a whole role.
        clamp_lo = sorted_ys[int(0.05 * len(sorted_ys))]
        clamp_hi = sorted_ys[int(0.95 * len(sorted_ys)) - 1]
        fades[role] = RoleFade(
            slope=slope, intercept=intercept, clamp_lo=clamp_lo, clamp_hi=clamp_hi
        )
    return fades


def fit_role_fades(system: str) -> dict[str, RoleFade]:
    """The I/O shell over `fit_fades`: two reads, then the pure fit."""
    return fit_fades(*_training_data(system), system)


def _training_data(
    system: str,
) -> tuple[list[BiasRow], dict[tuple[str, str], PriorStats]]:
    """The two tables every stage of the model reads. One session, both reads."""
    with _db.session() as handle:
        return (
            _db.load_bias_rows(handle, system, seasons=set(TRAIN_SEASONS), min_qi=MIN_QI),
            _db.load_prior_stats(handle, system),
        )


def discount_factors(bias_rows: Sequence[BiasRow]) -> dict[str, float]:
    """One factor per allowlisted club, from the median drift of its players. Pure.

    The median rather than the mean, for the same reason `RoleFade` fits on a log ratio:
    one breakout season must not reprice a whole club.
    """
    by_team: dict[str, list[float]] = defaultdict(list)
    for row in bias_rows:
        by_team[row.squadra].append(row.pct_delta)

    factors = {}
    for team in TEAM_DISCOUNT_ALLOWLIST:
        pcts = by_team.get(team, [])
        if not pcts:
            continue
        median_pct = statistics.median(pcts)
        factors[team] = 1.0 + median_pct / 100.0
    return factors


def team_discount_factors(system: str) -> dict[str, float]:
    """The I/O shell over `discount_factors`."""
    return discount_factors(_training_data(system)[0])


@dataclass(frozen=True)
class TargetPriceRow:
    id: str
    nome: str
    squadra: str
    role: str
    macro_role: str
    qi: int
    prior_media_fantavoto: float | None
    predicted_pct_delta: float | None
    team_factor: float
    target_price: int
    flags: str


def price_universe(
    universe: Sequence[PlayerQuote],
    prior_stats: Mapping[tuple[str, str], PriorStats],
    fades: Mapping[str, RoleFade],
    team_factors: Mapping[str, float],
    system: str,
) -> list[TargetPriceRow]:
    """A target price for every player in `universe`. Pure.

    The flag chain is an `elif`, so the order is the precedence: a cheap goalkeeper reads
    `floor_qi`, not `goalkeeper_no_fade`. Every branch that sets a flag also leaves
    `adjustment_factor` at 1.0, so a flagged player is priced at `qi` times his club's
    factor and nothing else.
    """
    out: list[TargetPriceRow] = []
    for row in universe:
        player_id = row.id
        role = row.role
        role_bucket = macro_role(role, system)
        squadra = row.squadra
        qi = row.qi

        flags = []
        prior = prior_stats.get((player_id, PRIOR_SEASON_FOR_TARGET))
        predicted_pct_delta: float | None = None
        adjustment_factor = 1.0

        if qi <= MIN_QI:
            flags.append("floor_qi")
        elif role_bucket == GOALKEEPER_MACRO:
            flags.append("goalkeeper_no_fade")
        elif prior is None:
            flags.append("no_prior_data")
        elif not (REGULAR_APPEARANCES_LO <= prior.partite_giocate <= REGULAR_APPEARANCES_HI):
            flags.append("thin_prior_sample_no_fade")
        elif role_bucket in fades:
            adjustment_factor = fades[role_bucket].predict_factor(prior.media_fantavoto)
            predicted_pct_delta = (adjustment_factor - 1.0) * 100.0
        else:
            flags.append("no_role_fade_model")

        team_factor = team_factors.get(squadra, 1.0)
        if squadra in team_factors:
            flags.append(f"team_discount({squadra})")

        target = max(1, round(qi * adjustment_factor * team_factor))

        out.append(
            TargetPriceRow(
                id=player_id,
                nome=row.nome,
                squadra=squadra,
                role=role,
                macro_role=role_bucket,
                qi=qi,
                prior_media_fantavoto=prior.media_fantavoto if prior else None,
                predicted_pct_delta=predicted_pct_delta,
                team_factor=team_factor,
                target_price=target,
                flags=";".join(flags),
            )
        )
    return out


def compute_target_prices(system: str) -> list[TargetPriceRow]:
    """The I/O shell: read once, then fit, discount and price. All pure from here."""
    bias_rows, prior_stats = _training_data(system)
    with _db.session() as handle:
        universe = _db.load_quotes(handle, system, seasons={TARGET_SEASON})

    return price_universe(
        universe,
        prior_stats,
        fit_fades(bias_rows, prior_stats, system),
        discount_factors(bias_rows),
        system,
    )


@dataclass(frozen=True)
class FadeSummary:
    """One fitted fade, with the number of observations behind it.

    `RoleFade` does not carry the count, and `run` used to recover it by re-running both
    training queries and re-applying the appearance filter -- two extra full reads of the
    database, for one column of one table. It is carried now.
    """

    role: str
    observations: int
    fade: RoleFade


@dataclass(frozen=True)
class PricingReport:
    """Everything a pricing run has to say, assembled without saying it."""

    system: str
    fades: tuple[FadeSummary, ...]
    team_factors: Mapping[str, float]
    #: What the upsert returned. Not `len(rows)` -- a row computed and not written is
    #: exactly the failure this number exists to show.
    stored: int
    biggest_bumps: tuple[TargetPriceRow, ...]
    biggest_cuts: tuple[TargetPriceRow, ...]
    flag_counts: Mapping[str, int]


def build_report(
    *,
    system: str,
    fades: Mapping[str, RoleFade],
    observations: Mapping[str, int],
    team_factors: Mapping[str, float],
    rows: Sequence[TargetPriceRow],
    stored: int,
    top_n: int,
) -> PricingReport:
    """Assemble the report. Pure.

    Movers are ranked by the credit difference rather than the ratio: +20 on a qi of 10
    and +20 on a qi of 200 cost the same at the auction.
    """
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        for flag in row.flags.split(";"):
            if flag:
                counts[flag.split("(")[0]] += 1

    return PricingReport(
        system=system,
        fades=tuple(
            FadeSummary(role=role, observations=observations.get(role, 0), fade=fade)
            for role, fade in fades.items()
        ),
        team_factors=dict(team_factors),
        stored=stored,
        biggest_bumps=tuple(sorted(rows, key=lambda r: -(r.target_price - r.qi))[:top_n]),
        biggest_cuts=tuple(sorted(rows, key=lambda r: (r.target_price - r.qi))[:top_n]),
        flag_counts=dict(counts),
    )


def run(system: str = "classic", top_n: int = 15) -> PricingReport:
    """Fit, price, upsert, and report. No presentation: see `interface/app.py`.

    The three stages read the training tables once between them, and the fade counts come
    from the same rows the fit used rather than from two more queries.
    """
    bias_rows, prior_stats = _training_data(system)
    fades = fit_fades(bias_rows, prior_stats, system)
    team_factors = discount_factors(bias_rows)

    with _db.session() as handle:
        universe = _db.load_quotes(handle, system, seasons={TARGET_SEASON})
    rows = price_universe(universe, prior_stats, fades, team_factors, system)

    # Database only. The CSV writer was removed on 2026-08-26, once the port had been
    # verified row-for-row against the pre-port capture. `target_price` is the record.
    with _db.session() as handle:
        stored = _db.upsert_target_price(
            handle,
            system,
            TARGET_SEASON,
            [
                {
                    "player_id": int(r.id),
                    "squadra": r.squadra,
                    "role": r.role,
                    "macro_role": r.macro_role,
                    "qi": r.qi,
                    "prior_media_fantavoto": r.prior_media_fantavoto,
                    "predicted_pct_delta": r.predicted_pct_delta,
                    "team_factor": r.team_factor,
                    "target_price": r.target_price,
                    "flags": r.flags,
                }
                for r in rows
            ],
        )

    return build_report(
        system=system,
        fades=fades,
        observations=count_observations(bias_rows, prior_stats, system),
        team_factors=team_factors,
        rows=rows,
        stored=stored,
        top_n=top_n,
    )
