"""Audit how far each past season's opening QI diverged from its final QA.

QI (Quotazione Iniziale) is pure editorial judgment set before a ball is
kicked — history, potential, reputation (see rules/algoritmo-quotazioni.md).
QA (Quotazione Attuale, here the rounded QAA) is what the site's algorithm
settles on after a full season of real results feeding the weekly update
rules. So QA at season-end is the closest proxy we have for "what this
player was actually worth, in hindsight" — comparing it to the season's
opening QI is a first-pass, low-effort measure of editorial mispricing,
using only the quotazioni CSVs (no voti/statistiche join yet).

Confirmed 2026-08-19: for the 2026/27 season qi == qa on every row (season
hasn't started, no updates have run yet) — so 2026/27 is excluded by
default, there is nothing to compare it against.

Usage:
    python scripts/analyze_qi_bias.py [--data-dir data] [--out-dir data]
        [--seasons 2022/23 2023/24 2024/25 2025/26] [--top-n 15]

Writes, per system:
    <out-dir>/qi_bias_classic.csv
    <out-dir>/qi_bias_mantra.csv
each with one row per (season, player): qi, qa, delta, pct_delta — the raw
material for the next step (joining against actual output to see whether
the mispricing was *justified* by performance or not).
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

DEFAULT_SEASONS = ["2022/23", "2023/24", "2024/25", "2025/26"]

# (filename, role column name) — classic has one role letter per player,
# mantra has a ";"-separated compound code (e.g. "B;DD;E"); both are used
# as-is, no attempt to collapse mantra's compound roles to a primary one.
SYSTEMS = {
    "classic": ("quotazioni_classic.csv", "ruolo_codice"),
    "mantra": ("quotazioni_mantra.csv", "ruoli_codice"),
}


@dataclass(frozen=True)
class PlayerQuote:
    stagione: str
    id: str
    nome: str
    squadra: str
    role: str
    qi: int
    qa: int
    fvm: int

    @property
    def delta(self) -> int:
        return self.qa - self.qi

    @property
    def pct_delta(self) -> float:
        return (self.delta / self.qi) * 100 if self.qi else 0.0


def load_quotes(path: Path, role_col: str, seasons: set[str]) -> list[PlayerQuote]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            PlayerQuote(
                stagione=row["stagione"],
                id=row["id"],
                nome=row["nome"],
                squadra=row["squadra"],
                role=row[role_col],
                qi=int(row["qi"]),
                qa=int(row["qa"]),
                fvm=int(row["fvm"]),
            )
            for row in csv.DictReader(f)
            if row["stagione"] in seasons
        ]


def summarize(quotes: list[PlayerQuote]) -> dict[str, float]:
    deltas = [q.delta for q in quotes]
    pct_deltas = [q.pct_delta for q in quotes]
    abs_pct = [abs(p) for p in pct_deltas]
    return {
        "n": len(quotes),
        "mean_delta": statistics.mean(deltas),
        "mean_abs_delta": statistics.mean(abs(d) for d in deltas),
        "mean_pct_delta": statistics.mean(pct_deltas),
        "mean_abs_pct_delta": statistics.mean(abs_pct),
        "pct_off_gt_30": 100 * sum(1 for p in abs_pct if p > 30) / len(quotes),
    }


def print_group_table(title: str, groups: dict[str, list[PlayerQuote]], key_header: str) -> None:
    table = Table(title=title)
    table.add_column(key_header)
    table.add_column("n", justify="right")
    table.add_column("mean Δ", justify="right")
    table.add_column("mean |Δ|", justify="right")
    table.add_column("mean %Δ", justify="right")
    table.add_column("mean |%Δ|", justify="right")
    table.add_column("% off >30%", justify="right")
    for key in sorted(groups, key=lambda k: -len(groups[k])):
        s = summarize(groups[key])
        table.add_row(
            key,
            str(int(s["n"])),
            f"{s['mean_delta']:+.2f}",
            f"{s['mean_abs_delta']:.2f}",
            f"{s['mean_pct_delta']:+.1f}%",
            f"{s['mean_abs_pct_delta']:.1f}%",
            f"{s['pct_off_gt_30']:.1f}%",
        )
    console.print(table)


def print_extremes(quotes: list[PlayerQuote], top_n: int) -> None:
    most_undervalued = sorted(quotes, key=lambda q: -q.pct_delta)[:top_n]
    most_overvalued = sorted(quotes, key=lambda q: q.pct_delta)[:top_n]

    console.print(f"\n  Top {top_n} most UNDERVALUED at QI (QA ended up far above QI):")
    for q in most_undervalued:
        console.print(
            f"    {q.stagione} {q.nome:20s} {q.squadra:4s} {q.role:8s} "
            f"qi={q.qi:>3d} qa={q.qa:>3d} Δ={q.delta:+3d} ({q.pct_delta:+.0f}%)"
        )

    console.print(f"\n  Top {top_n} most OVERVALUED at QI (QA ended up far below QI):")
    for q in most_overvalued:
        console.print(
            f"    {q.stagione} {q.nome:20s} {q.squadra:4s} {q.role:8s} "
            f"qi={q.qi:>3d} qa={q.qa:>3d} Δ={q.delta:+3d} ({q.pct_delta:+.0f}%)"
        )


def write_csv(path: Path, quotes: list[PlayerQuote]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["stagione", "id", "nome", "squadra", "role", "qi", "qa", "fvm", "delta", "pct_delta"]
        )
        for q in quotes:
            writer.writerow(
                [q.stagione, q.id, q.nome, q.squadra, q.role, q.qi, q.qa, q.fvm, q.delta, f"{q.pct_delta:.2f}"]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    parser.add_argument("--seasons", nargs="+", default=DEFAULT_SEASONS)
    parser.add_argument("--top-n", type=int, default=15)
    args = parser.parse_args()

    seasons = set(args.seasons)

    for system, (filename, role_col) in SYSTEMS.items():
        path = args.data_dir / filename
        quotes = load_quotes(path, role_col, seasons)
        if not quotes:
            console.print(f"[yellow]{system}: no rows for seasons {sorted(seasons)}, skipping[/yellow]")
            continue

        console.rule(f"[bold]{system.upper()}[/bold]  ({path}, {len(quotes)} rows)")

        overall = summarize(quotes)
        console.print(
            f"  overall: n={int(overall['n'])} mean_delta={overall['mean_delta']:+.2f} "
            f"mean_abs_pct_delta={overall['mean_abs_pct_delta']:.1f}% "
            f"pct_off_gt_30%={overall['pct_off_gt_30']:.1f}%"
        )

        by_season: dict[str, list[PlayerQuote]] = defaultdict(list)
        by_role: dict[str, list[PlayerQuote]] = defaultdict(list)
        for q in quotes:
            by_season[q.stagione].append(q)
            by_role[q.role].append(q)

        print_group_table(f"{system} — QI vs QA by season", by_season, "season")
        print_group_table(f"{system} — QI vs QA by role (pooled)", by_role, "role")
        print_extremes(quotes, args.top_n)

        out_path = args.out_dir / f"qi_bias_{system}.csv"
        write_csv(out_path, quotes)
        console.print(f"\n  wrote {out_path} ({len(quotes)} rows)\n")


if __name__ == "__main__":
    main()
