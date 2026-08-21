"""Sketch of a target_price function for the 2026/27 asta iniziale, classic or mantra.

Not wired into src/fantabot/data_sources/ yet — this is a research script that
mirrors StatsSource.target_price(player) -> int's shape so it's a drop-in
later, once someone decides it's good enough to trust with real credits.

Starts from QI(2026/27) (== QA(2026/27), season hasn't started — confirmed
earlier) and applies two adjustments, both backed by scripts/join_qi_bias_performance.py
and scripts/analyze_qi_bias_by_team.py's output on 2022/23-2025/26:

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
   from qi_bias_classic.csv rather than hardcoded, so it stays in sync if
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

Mantra role bucketing: Mantra's ~30 role codes in quotazioni_mantra.csv are
mostly compound (e.g. "B;DD;DS", "W;T;A") and mostly tiny — the exact-code
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
(quotazioni_mantra.csv 2026/27) and it's only ever "M", "M;C", or "E;M",
never paired with a defensive code. "M;C" alone is 66/259 compound-role
players (Lobotka, Locatelli, Modric, Calhanoglu, Rovella...) — a real mix
of destroyers and advanced playmakers, but never defenders. M now folds
into MID instead, alongside C and E.

Usage:
    python scripts/target_price.py [--system classic|mantra] [--data-dir data] [--out ...]
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

TRAIN_SEASONS = ["2023/24", "2024/25", "2025/26"]
PREV_OF_TRAIN = dict(zip(TRAIN_SEASONS, ["2022/23", "2023/24", "2024/25"], strict=True))
TARGET_SEASON = "2026/27"
PRIOR_SEASON_FOR_TARGET = "2025/26"

MIN_QI = 2  # floor-effect guard, consistent with analyze_qi_bias_by_team.py / analyze_low_minutes_bias.py
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


def parse_decimal(raw: str) -> float:
    return float(raw.replace(",", "."))


@dataclass(frozen=True)
class PriorStats:
    partite_giocate: int
    media_fantavoto: float


@dataclass(frozen=True)
class BiasRow:
    stagione: str
    id: str
    nome: str
    squadra: str
    role: str
    qi: int
    qa: int
    pct_delta: float

    @property
    def log_ratio(self) -> float:
        return math.log(self.qa / self.qi)


def load_prior_stats(path: Path) -> dict[tuple[str, str], PriorStats]:
    by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_key[(row["id"], row["stagione"])].append(row)

    out: dict[tuple[str, str], PriorStats] = {}
    for key, rows in by_key.items():
        fantavoti = [parse_decimal(r["media_fantavoto"]) for r in rows if r["media_fantavoto"] not in ("", "0,0")]
        if not fantavoti:
            continue
        out[key] = PriorStats(
            partite_giocate=int(rows[0]["partite_giocate"]),
            media_fantavoto=statistics.mean(fantavoti),
        )
    return out


def load_bias_rows(path: Path, seasons: set[str], min_qi: int) -> list[BiasRow]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            BiasRow(
                stagione=row["stagione"],
                id=row["id"],
                nome=row["nome"],
                squadra=row["squadra"],
                role=row["role"],
                qi=int(row["qi"]),
                qa=int(row["qa"]),
                pct_delta=float(row["pct_delta"]),
            )
            for row in csv.DictReader(f)
            if row["stagione"] in seasons and int(row["qi"]) > min_qi
        ]


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


def fit_role_fades(data_dir: Path, system: str) -> dict[str, RoleFade]:
    prior_stats = load_prior_stats(data_dir / f"statistiche_{system}.csv")
    bias_rows = load_bias_rows(data_dir / f"qi_bias_{system}.csv", set(TRAIN_SEASONS), MIN_QI)

    by_role: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in bias_rows:
        role = macro_role(row.role, system)
        if role == GOALKEEPER_MACRO:
            continue
        prior = prior_stats.get((row.id, PREV_OF_TRAIN[row.stagione]))
        if prior is None or not (REGULAR_APPEARANCES_LO <= prior.partite_giocate <= REGULAR_APPEARANCES_HI):
            continue
        by_role[role].append((prior.media_fantavoto, row.log_ratio))

    fades: dict[str, RoleFade] = {}
    for role, pairs in by_role.items():
        if len(pairs) < 20:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        slope, intercept = statistics.linear_regression(xs, ys)
        sorted_ys = sorted(ys)
        clamp_lo = sorted_ys[int(0.05 * len(sorted_ys))]
        clamp_hi = sorted_ys[int(0.95 * len(sorted_ys)) - 1]
        fades[role] = RoleFade(slope=slope, intercept=intercept, clamp_lo=clamp_lo, clamp_hi=clamp_hi)
    return fades


def team_discount_factors(data_dir: Path, system: str) -> dict[str, float]:
    bias_rows = load_bias_rows(data_dir / f"qi_bias_{system}.csv", set(TRAIN_SEASONS), MIN_QI)
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


def compute_target_prices(data_dir: Path, system: str) -> list[TargetPriceRow]:
    fades = fit_role_fades(data_dir, system)
    team_factors = team_discount_factors(data_dir, system)
    prior_stats = load_prior_stats(data_dir / f"statistiche_{system}.csv")

    role_col = "ruolo_codice" if system == "classic" else "ruoli_codice"
    with (data_dir / f"quotazioni_{system}.csv").open(newline="", encoding="utf-8") as f:
        target_universe = [row for row in csv.DictReader(f) if row["stagione"] == TARGET_SEASON]

    out: list[TargetPriceRow] = []
    for row in target_universe:
        player_id = row["id"]
        role = row[role_col]
        role_bucket = macro_role(role, system)
        squadra = row["squadra"]
        qi = int(row["qi"])

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
                nome=row["nome"],
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--system", choices=["classic", "mantra"], default="classic")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--top-n", type=int, default=15)
    args = parser.parse_args()
    out_path = args.out or args.data_dir / f"target_price_2026_27_{args.system}.csv"

    fades = fit_role_fades(args.data_dir, args.system)
    console.print(f"[bold]{args.system}: fitted role fades (log(qa/qi) ~ prior_media_fantavoto, OLS):[/bold]")
    fade_table = Table()
    fade_table.add_column("macro role")
    fade_table.add_column("n", justify="right")
    fade_table.add_column("slope", justify="right")
    fade_table.add_column("intercept", justify="right")
    fade_table.add_column("clamp range (as %)", justify="right")
    # RoleFade doesn't carry n; recompute just for display
    prior_stats_dbg = load_prior_stats(args.data_dir / f"statistiche_{args.system}.csv")
    bias_rows_dbg = load_bias_rows(args.data_dir / f"qi_bias_{args.system}.csv", set(TRAIN_SEASONS), MIN_QI)
    n_by_role: dict[str, int] = defaultdict(int)
    for row in bias_rows_dbg:
        role = macro_role(row.role, args.system)
        prior = prior_stats_dbg.get((row.id, PREV_OF_TRAIN[row.stagione]))
        if prior is not None and REGULAR_APPEARANCES_LO <= prior.partite_giocate <= REGULAR_APPEARANCES_HI:
            n_by_role[role] += 1
    for role, fade in fades.items():
        pct_lo = (math.exp(fade.clamp_lo) - 1.0) * 100.0
        pct_hi = (math.exp(fade.clamp_hi) - 1.0) * 100.0
        fade_table.add_row(
            role,
            str(n_by_role[role]),
            f"{fade.slope:+.3f}",
            f"{fade.intercept:+.3f}",
            f"[{pct_lo:+.0f}%, {pct_hi:+.0f}%]",
        )
    console.print(fade_table)

    team_factors = team_discount_factors(args.data_dir, args.system)
    console.print(f"\n[bold]Team discount factors applied:[/bold] {team_factors}\n")

    rows = compute_target_prices(args.data_dir, args.system)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["id", "nome", "squadra", "role", "macro_role", "qi", "prior_media_fantavoto", "predicted_pct_delta",
             "team_factor", "target_price", "flags"]
        )
        for r in rows:
            writer.writerow(
                [
                    r.id, r.nome, r.squadra, r.role, r.macro_role, r.qi,
                    f"{r.prior_media_fantavoto:.2f}" if r.prior_media_fantavoto is not None else "",
                    f"{r.predicted_pct_delta:+.1f}" if r.predicted_pct_delta is not None else "",
                    f"{r.team_factor:.3f}", r.target_price, r.flags,
                ]
            )
    console.print(f"wrote {out_path} ({len(rows)} rows)\n")

    biggest_bumps = sorted(rows, key=lambda r: -(r.target_price - r.qi))[: args.top_n]
    biggest_cuts = sorted(rows, key=lambda r: (r.target_price - r.qi))[: args.top_n]

    console.print(f"Top {args.top_n} biggest UPWARD adjustments (target > qi):", markup=False)
    for r in biggest_bumps:
        console.print(
            f"  {r.nome:20s} {r.squadra:4s} {r.role:8s}({r.macro_role:7s}) qi={r.qi:>3d} -> target={r.target_price:>3d}  flags={r.flags}",
            markup=False,
        )

    console.print(f"\nTop {args.top_n} biggest DOWNWARD adjustments (target < qi):", markup=False)
    for r in biggest_cuts:
        console.print(
            f"  {r.nome:20s} {r.squadra:4s} {r.role:8s}({r.macro_role:7s}) qi={r.qi:>3d} -> target={r.target_price:>3d}  flags={r.flags}",
            markup=False,
        )

    flag_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        for flag in r.flags.split(";"):
            if flag:
                flag_counts[flag.split("(")[0]] += 1
    console.print(f"\nFlag counts: {dict(flag_counts)}")


if __name__ == "__main__":
    main()
