"""Roll up scripts/analyze_qi_bias.py's per-player QI-vs-QA bias by team.

Reads data/qi_bias_{classic,mantra}.csv (produced by analyze_qi_bias.py) and
groups by squadra to see whether some teams' whole rosters were
systematically mispriced at QI time, and whether that bias repeats across
seasons for the same team (persistent signal) or is a one-off (noise).

squadra here comes straight from quotazioni_classic.csv / quotazioni_mantra.csv
via the qi_bias CSVs — that field is scraped per-player, season-aggregate,
and confirmed clean (unlike voti.csv/bonus_malus.csv, which mislabel squadra
as the fixture's home team for every row in the match block — see the
GiornataParser docstring in scrape_voti.py; that bug is orthogonal to this
script and doesn't affect it).

QI floor effect: players priced at qi<=2 produce huge, meaningless %-deltas
off a tiny base (e.g. qi=1 -> qa=12 is "+1100%" for an 11-credit swing).
Default excludes qi<=2 from the ranking so team-level %-signal isn't just
"which team has the most 1cr fliers who got a run of games." Absolute delta
is reported alongside regardless.

Reports both mean and median %-delta per team: a team where the mean is
large but the median is near zero (or opposite sign) means the "team-wide"
bias is really 1-2 outlier players dragging the average, not a genuine
squad-wide mispricing pattern — flagged as "outlier-driven" in the table.

Reads from Postgres: `docker compose up -d && fantabot db-import --all` first.

Usage:
    python scripts/analyze_qi_bias_by_team.py [--min-qi 3] [--top-n 15]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))

import _db

console = Console()

SYSTEMS = ["classic", "mantra"]


@dataclass(frozen=True)
class BiasRow:
    stagione: str
    squadra: str
    qi: int
    delta: int
    pct_delta: float


def summarize(rows: list[BiasRow]) -> dict[str, float]:
    deltas = [r.delta for r in rows]
    pct_deltas = [r.pct_delta for r in rows]
    return {
        "n": len(rows),
        "mean_delta": statistics.mean(deltas),
        "median_delta": statistics.median(deltas),
        "mean_pct_delta": statistics.mean(pct_deltas),
        "median_pct_delta": statistics.median(pct_deltas),
        "mean_abs_pct_delta": statistics.mean(abs(p) for p in pct_deltas),
    }


def sign_consistency(rows_by_season: dict[str, list[BiasRow]]) -> tuple[float, int]:
    """Fraction of seasons whose mean pct_delta shares the sign of the pooled mean, and season count."""
    season_means = [statistics.mean(r.pct_delta for r in rows) for rows in rows_by_season.values()]
    overall_sign = statistics.mean(season_means) >= 0
    matches = sum(1 for m in season_means if (m >= 0) == overall_sign)
    return matches / len(season_means), len(season_means)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-qi", type=int, default=2, help="exclude rows with qi <= this (floor-effect guard)")
    parser.add_argument("--top-n", type=int, default=15)
    args = parser.parse_args()

    for system in SYSTEMS:
        with _db.session() as handle:
            rows = _db.load_bias_rows(handle, system, min_qi=args.min_qi)
        if not rows:
            console.print(f"[yellow]{system}: no rows after qi>{args.min_qi} filter, skipping[/yellow]")
            continue

        console.rule(f"[bold]{system.upper()}[/bold]  ({len(rows)} rows after qi>{args.min_qi} filter)")

        by_team: dict[str, list[BiasRow]] = defaultdict(list)
        by_team_season: dict[str, dict[str, list[BiasRow]]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            by_team[r.squadra].append(r)
            by_team_season[r.squadra][r.stagione].append(r)

        table = Table(title=f"{system} — QI bias by team (pooled across seasons present)")
        table.add_column("team")
        table.add_column("seasons", justify="right")
        table.add_column("n", justify="right")
        table.add_column("mean %Δ", justify="right")
        table.add_column("median %Δ", justify="right")
        table.add_column("mean |%Δ|", justify="right")
        table.add_column("sign consistency", justify="right")
        table.add_column("outlier-driven?", justify="right")

        team_stats = []
        for team, team_rows in by_team.items():
            s = summarize(team_rows)
            consistency, n_seasons = sign_consistency(by_team_season[team])
            team_stats.append((team, s, consistency, n_seasons))

        # sort by strength of pooled signal, most systematically underpriced first
        team_stats.sort(key=lambda t: -t[1]["mean_pct_delta"])

        for team, s, consistency, n_seasons in team_stats:
            # mean and median disagreeing on sign, or median much weaker than mean,
            # means the "team-wide" bias is really a handful of outlier players
            outlier_driven = (s["mean_pct_delta"] >= 0) != (s["median_pct_delta"] >= 0) or (
                abs(s["median_pct_delta"]) < abs(s["mean_pct_delta"]) * 0.4
            )
            table.add_row(
                team,
                str(n_seasons),
                str(int(s["n"])),
                f"{s['mean_pct_delta']:+.1f}%",
                f"{s['median_pct_delta']:+.1f}%",
                f"{s['mean_abs_pct_delta']:.1f}%",
                f"{consistency * 100:.0f}%",
                "yes" if outlier_driven else "no",
            )
        console.print(table)

        console.print(
            f"\n  Most systematically UNDERPRICED teams (top {args.top_n} by mean %Δ, "
            f"high sign-consistency = same direction every season present):"
        )
        for team, s, consistency, n_seasons in team_stats[: args.top_n]:
            flag = (
                "  [outlier-driven]"
                if (s["mean_pct_delta"] >= 0) != (s["median_pct_delta"] >= 0)
                or abs(s["median_pct_delta"]) < abs(s["mean_pct_delta"]) * 0.4
                else ""
            )
            console.print(
                f"    {team:5s} n={int(s['n']):>3d} seasons={n_seasons} "
                f"mean%Δ={s['mean_pct_delta']:+6.1f}% median%Δ={s['median_pct_delta']:+6.1f}% "
                f"consistency={consistency * 100:.0f}%{flag}"
            )

        console.print(
            f"\n  Most systematically OVERPRICED teams (top {args.top_n} by mean %Δ, ascending):"
        )
        for team, s, consistency, n_seasons in team_stats[-args.top_n :][::-1]:
            flag = (
                "  [outlier-driven]"
                if (s["mean_pct_delta"] >= 0) != (s["median_pct_delta"] >= 0)
                or abs(s["median_pct_delta"]) < abs(s["mean_pct_delta"]) * 0.4
                else ""
            )
            console.print(
                f"    {team:5s} n={int(s['n']):>3d} seasons={n_seasons} "
                f"mean%Δ={s['mean_pct_delta']:+6.1f}% median%Δ={s['median_pct_delta']:+6.1f}% "
                f"consistency={consistency * 100:.0f}%{flag}"
            )
        console.print()


if __name__ == "__main__":
    main()
