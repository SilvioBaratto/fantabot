"""Isolate the low-minutes-good-rate pattern flagged by join_qi_bias_performance.py.

That script found a pooled regression-to-mean effect: corr(prior_media_fantavoto,
pct_delta) ~= -0.18, i.e. players who scored well last season tend to get
overpriced this season and vice versa. But its "biggest misses" list was
full of players with a *decent* prior rate on a *thin* sample (Svilar 4.83
over 3 apps, Leali 5.17 over 2 apps) still getting floored to qi=1 anyway.
That looks like a different mechanism from pooled mean-reversion: editors
may over-discount thin samples specifically, regardless of how good the
rate was, which would show up as a POSITIVE correlation within the
low-appearance subgroup even though the pooled correlation is negative.
This script tests that directly.

Classic system only (4 clean roles p/d/c/a) — mantra's compound role codes
fragment sample sizes for no benefit here, since appearance count and
fantamedia are role-agnostic scalars; classic's role split is enough to
get a stable "above/below role median rate" threshold. Excludes qi<=2 rows
(floor-effect guard, same as analyze_qi_bias_by_team.py) and, like
join_qi_bias_performance.py, only uses 2023/24-2025/26 (each has a real
prior season inside our data window).

Usage:
    python scripts/analyze_low_minutes_bias.py [--data-dir data] [--min-qi 2]
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

SEASON_ORDER = ["2022/23", "2023/24", "2024/25", "2025/26"]
PREV_SEASON = dict(zip(SEASON_ORDER[1:], SEASON_ORDER[:-1], strict=True))

# (label, lo, hi) inclusive, chosen from the actual partite_giocate distribution
# (deciles: 1/5/10.6/16/21/25/30/34) — most players cluster at the low end
APPEARANCE_TIERS = [
    ("1-4 (very thin)", 1, 4),
    ("5-14 (thin)", 5, 14),
    ("15-24 (moderate)", 15, 24),
    ("25-38 (regular)", 25, 38),
]


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
    pct_delta: float


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


def load_bias_rows(path: Path, min_qi: int) -> list[BiasRow]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            BiasRow(
                stagione=row["stagione"],
                id=row["id"],
                nome=row["nome"],
                squadra=row["squadra"],
                role=row["role"],
                qi=int(row["qi"]),
                pct_delta=float(row["pct_delta"]),
            )
            for row in csv.DictReader(f)
            if row["stagione"] in PREV_SEASON and int(row["qi"]) > min_qi
        ]


def safe_correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    return statistics.correlation(xs, ys)


def tier_for(apps: int) -> str | None:
    for label, lo, hi in APPEARANCE_TIERS:
        if lo <= apps <= hi:
            return label
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--min-qi", type=int, default=2)
    args = parser.parse_args()

    prior_stats = load_prior_stats(args.data_dir / "statistiche_classic.csv")
    bias_rows = load_bias_rows(args.data_dir / "qi_bias_classic.csv", args.min_qi)

    joined: list[tuple[BiasRow, PriorStats]] = []
    for row in bias_rows:
        prior = prior_stats.get((row.id, PREV_SEASON[row.stagione]))
        if prior is not None:
            joined.append((row, prior))

    console.rule(f"[bold]CLASSIC[/bold] — low-minutes-good-rate isolation (qi>{args.min_qi}, n={len(joined)})")

    # role-relative median prior fantamedia, pooled across seasons, as the "good rate" threshold
    fm_by_role: dict[str, list[float]] = defaultdict(list)
    for r, p in joined:
        fm_by_role[r.role].append(p.media_fantavoto)
    role_median = {role: statistics.median(vals) for role, vals in fm_by_role.items()}
    console.print("  role-relative 'good rate' threshold (median prior media_fantavoto): ", end="")
    console.print({role: round(m, 2) for role, m in role_median.items()})

    # 1. correlation within each appearance tier (does the pooled -0.18 flip sign for thin samples?)
    table = Table(title="corr(prior_media_fantavoto, pct_delta) by appearance tier")
    table.add_column("tier")
    table.add_column("n", justify="right")
    table.add_column("mean prior fm", justify="right")
    table.add_column("mean pct_delta", justify="right")
    table.add_column("median pct_delta", justify="right")
    table.add_column("correlation", justify="right")

    tier_groups: dict[str, list[tuple[BiasRow, PriorStats]]] = defaultdict(list)
    for r, p in joined:
        tier = tier_for(p.partite_giocate)
        if tier:
            tier_groups[tier].append((r, p))

    for label, _, _ in APPEARANCE_TIERS:
        pairs = tier_groups.get(label, [])
        if not pairs:
            continue
        fms = [p.media_fantavoto for _, p in pairs]
        pcts = [r.pct_delta for r, _ in pairs]
        corr = safe_correlation(fms, pcts)
        table.add_row(
            label,
            str(len(pairs)),
            f"{statistics.mean(fms):.2f}",
            f"{statistics.mean(pcts):+.1f}%",
            f"{statistics.median(pcts):+.1f}%",
            f"{corr:+.3f}" if corr is not None else "n/a",
        )
    console.print(table)

    # 2. the actual 2x2 test: within each tier, split above/below role-relative median rate
    table2 = Table(title="mean/median pct_delta: appearance tier x rate-vs-role-median")
    table2.add_column("tier")
    table2.add_column("rate")
    table2.add_column("n", justify="right")
    table2.add_column("mean pct_delta", justify="right")
    table2.add_column("median pct_delta", justify="right")

    for label, _, _ in APPEARANCE_TIERS:
        pairs = tier_groups.get(label, [])
        if not pairs:
            continue
        above = [(r, p) for r, p in pairs if p.media_fantavoto >= role_median.get(r.role, 6.0)]
        below = [(r, p) for r, p in pairs if p.media_fantavoto < role_median.get(r.role, 6.0)]
        for sub_label, sub in (("above role median", above), ("below role median", below)):
            if not sub:
                continue
            pcts = [r.pct_delta for r, _ in sub]
            table2.add_row(
                label,
                sub_label,
                str(len(sub)),
                f"{statistics.mean(pcts):+.1f}%",
                f"{statistics.median(pcts):+.1f}%",
            )
    console.print(table2)

    console.print(
        "\n  Reading this: if 'thin, above-median' shows a much bigger mean/median pct_delta than\n"
        "  'thin, below-median', and the gap shrinks or vanishes in the 'regular' tier, that's the\n"
        "  low-minutes-good-rate underpricing pattern confirmed as its own effect — not just the\n"
        "  pooled regression-to-mean signal restated.\n"
    )


if __name__ == "__main__":
    main()
