"""Test whether QI misses were foreseeable from data editors already had, or genuine surprises.

For every qi_bias_{classic,mantra}.csv row (season N, how wrong QI(N) turned
out to be vs QA(N)), look up that same player's PRIOR season (N-1) output
from statistiche_{classic,mantra}.csv — media_fantavoto and partite_giocate,
both public knowledge before QI(N) was ever set. Two buckets:

  - no_prior_data: player has no row in our data for season N-1 (new to
    Serie A, transferred in from abroad, or came back from a season with
    zero appearances). QI(N) for these is necessarily a blind guess —
    can't be "wrong given the data" because there was no data.
  - has_prior_data: player has a real prior-season track record. If QI(N)'s
    error (pct_delta) correlates with prior_media_fantavoto, editors had
    the signal in hand and underweighted it (exploitable: bump price above
    QI for high-prior-fantamedia players). If it doesn't correlate, the
    miss was genuinely unpredictable from last year's output (noise, not
    a blind spot).

Uses only 2023/24-2025/26 qi_bias rows (each has a real prior season inside
our data window); 2022/23 is dropped from this join entirely since we can't
tell "no prior data because dataset starts here" apart from "no prior data
because genuinely new" — would bias the no_prior_data bucket.

Usage:
    python scripts/join_qi_bias_performance.py [--data-dir data] [--min-appearances 10] [--top-n 15]
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

SYSTEMS = ["classic", "mantra"]


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
    """(id, stagione) -> PriorStats, media_fantavoto averaged across the 3 fonte rows."""
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


def load_bias_rows(path: Path) -> list[BiasRow]:
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
            if row["stagione"] in PREV_SEASON
        ]


def safe_correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    return statistics.correlation(xs, ys)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--min-appearances", type=int, default=10)
    parser.add_argument("--top-n", type=int, default=15)
    args = parser.parse_args()

    for system in SYSTEMS:
        bias_path = args.data_dir / f"qi_bias_{system}.csv"
        stats_path = args.data_dir / f"statistiche_{system}.csv"
        if not bias_path.exists():
            console.print(f"[yellow]{system}: {bias_path} missing, run analyze_qi_bias.py first[/yellow]")
            continue

        console.rule(f"[bold]{system.upper()}[/bold]")

        prior_stats = load_prior_stats(stats_path)
        bias_rows = load_bias_rows(bias_path)

        has_prior: list[tuple[BiasRow, PriorStats]] = []
        no_prior: list[BiasRow] = []
        for row in bias_rows:
            prior = prior_stats.get((row.id, PREV_SEASON[row.stagione]))
            if prior is None:
                no_prior.append(row)
            else:
                has_prior.append((row, prior))

        console.print(f"  rows joined: {len(bias_rows)} (seasons {list(PREV_SEASON)}, 2022/23 excluded)")
        console.print(f"  no prior-season data: {len(no_prior)}  |  has prior-season data: {len(has_prior)}")

        no_prior_pct = [r.pct_delta for r in no_prior]
        has_prior_pct = [r.pct_delta for r, _ in has_prior]
        console.print(
            f"\n  no_prior_data bucket:  mean pct_delta={statistics.mean(no_prior_pct):+.1f}%  "
            f"mean |pct_delta|={statistics.mean(abs(p) for p in no_prior_pct):.1f}%"
        )
        console.print(
            f"  has_prior_data bucket: mean pct_delta={statistics.mean(has_prior_pct):+.1f}%  "
            f"mean |pct_delta|={statistics.mean(abs(p) for p in has_prior_pct):.1f}%"
        )

        # headline test: does the error correlate with info editors already had?
        all_prior_fm = [p.media_fantavoto for _, p in has_prior]
        all_pct = [r.pct_delta for r, _ in has_prior]
        r_fantamedia = safe_correlation(all_prior_fm, all_pct)
        console.print(
            f"\n  corr(prior_media_fantavoto, pct_delta) = {r_fantamedia:+.3f}"
            if r_fantamedia is not None
            else "\n  corr(prior_media_fantavoto, pct_delta): insufficient data"
        )

        # same test restricted to players editors had a real sample size for
        trusted = [(r, p) for r, p in has_prior if p.partite_giocate >= args.min_appearances]
        r_trusted = safe_correlation(
            [p.media_fantavoto for _, p in trusted], [r.pct_delta for r, _ in trusted]
        )
        console.print(
            f"  corr, restricted to prior_partite_giocate>={args.min_appearances} (n={len(trusted)}) = "
            f"{r_trusted:+.3f}"
            if r_trusted is not None
            else f"  corr, restricted to prior_partite_giocate>={args.min_appearances}: insufficient data"
        )

        # per-role breakdown (classic only has 4 roles; mantra's compound codes get noisy, still shown)
        by_role: dict[str, list[tuple[BiasRow, PriorStats]]] = defaultdict(list)
        for r, p in has_prior:
            by_role[r.role].append((r, p))

        table = Table(title=f"{system} — corr(prior fantamedia, this season's QI error) by role")
        table.add_column("role")
        table.add_column("n", justify="right")
        table.add_column("correlation", justify="right")
        for role, pairs in sorted(by_role.items(), key=lambda kv: -len(kv[1])):
            r_role = safe_correlation([p.media_fantavoto for _, p in pairs], [r.pct_delta for r, _ in pairs])
            table.add_row(role, str(len(pairs)), f"{r_role:+.3f}" if r_role is not None else "n/a")
        console.print(table)

        # biggest surprises: strong prior form, QI still badly wrong (both directions)
        surprises = sorted(has_prior, key=lambda t: -abs(t[0].pct_delta))
        console.print(f"\n  Top {args.top_n} biggest misses DESPITE having a prior-season track record:")
        for r, p in surprises[: args.top_n]:
            console.print(
                f"    {r.stagione} {r.nome:20s} {r.squadra:4s} {r.role:8s} "
                f"prior_media_fantavoto={p.media_fantavoto:5.2f} (n={p.partite_giocate:>2d} apps) "
                f"qi={r.qi:>3d} pct_delta={r.pct_delta:+.0f}%"
            )
        console.print()


if __name__ == "__main__":
    main()
