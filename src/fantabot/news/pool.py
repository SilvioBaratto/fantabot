"""The player universe: one row per quotato player, carrying both role systems.

You play two leagues, one Classic and one Mantra, and both draw from the same
Serie A pool — 523 players for 2026/27, identical in both quotazioni files. So
one news CSV serves both, and every row carries the Classic ``ruolo`` and the
Mantra tag side by side.

That means a join, and a join is a thing that can be silently wrong. An id
present in one file and missing from the other **raises**: nulling the Mantra tag
would ship rows whose ``ruoli_mantra`` is empty, and that column is the entire
Mantra half of the feature. A broken join must look like a broken join.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


class PoolJoinError(RuntimeError):
    """The two quotazioni files disagree about who exists."""


@dataclass(frozen=True)
class PoolPlayer:
    """One player as the news pipeline needs him. Frozen, like fantabot's other values."""

    id: str
    nome: str
    squadra: str
    ruolo: str
    """Classic role, spelled out as the CSVs spell it: Portiere/Difensore/..."""
    ruoli_mantra: str
    """The frozen late-July Mantra tag, ``;``-joined uppercase, e.g. ``"DD;DC"``."""


def load_pool(classic_path: Path, mantra_path: Path, season: str) -> list[PoolPlayer]:
    """Join the two quotazioni files on id for one season.

    Ordered by (squadra, nome) so resume, logs and diffs stay stable across runs.
    """
    classic = _rows_by_id(classic_path, season)
    mantra = _rows_by_id(mantra_path, season)

    if not classic and not mantra:
        raise PoolJoinError(
            f"no players for season {season!r} in {classic_path} or {mantra_path}; "
            f"a silent empty pool would make a cron run look successful"
        )

    only_classic = sorted(classic.keys() - mantra.keys())
    only_mantra = sorted(mantra.keys() - classic.keys())
    if only_classic or only_mantra:
        raise PoolJoinError(
            f"quotazioni files disagree for season {season!r}: "
            f"{len(classic)} classic rows vs {len(mantra)} mantra rows; "
            f"only in classic: {only_classic or '-'}; only in mantra: {only_mantra or '-'}"
        )

    players = [
        PoolPlayer(
            id=player_id,
            nome=row["nome"],
            squadra=row["squadra"],
            ruolo=row["ruolo"],
            ruoli_mantra=mantra[player_id]["ruoli_codice"],
        )
        for player_id, row in classic.items()
    ]
    return sorted(players, key=lambda p: (p.squadra, p.nome))


def _rows_by_id(path: Path, season: str) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["id"]: row for row in csv.DictReader(handle) if row["stagione"] == season}
