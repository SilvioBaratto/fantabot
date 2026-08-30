"""The player universe: one row per quotato player, carrying both role systems.

You play two leagues, one Classic and one Mantra, and both draw from the same
Serie A pool — 523 players for 2026/27, identical in both quotazioni files. So
one news CSV serves both, and every row carries the Classic ``ruolo`` and the
Mantra tag side by side.

That means a join, and a join is a thing that can be silently wrong. An id
present in one listone and missing from the other **raises**: nulling the Mantra
tag would ship rows whose ``ruoli_mantra`` is empty, and that column is the
entire Mantra half of the feature. A broken join must look like a broken join.

The pool comes from the ``quotazioni`` table now rather than the two CSVs.
``build_pool`` keeps every rule — the two raises and the ``(squadra, nome)``
ordering — and stays pure, so the join logic is still testable with dictionaries
and no database.

**The two reads that feed it live in ``pipeline.py``.** They were a ``load_pool``
shell here, with its repository import inside the function body, which made this
module read as pure while reaching Postgres — and, because ``prompt.py`` and
``store.py`` import ``PoolPlayer`` from here, dragged them into the database's
import graph too for the sake of one dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fantabot.data_sources.models import QuotazioneRow


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


def build_pool(
    classic: dict[str, QuotazioneRow],
    mantra: dict[str, QuotazioneRow],
    season: str,
) -> list[PoolPlayer]:
    """Join the two listoni on id. Pure — no session, no files.

    Ordered by (squadra, nome) so resume, logs and diffs stay stable across runs.
    """
    if not classic and not mantra:
        raise PoolJoinError(
            f"no players for season {season!r} in either listone; "
            f"a silent empty pool would make a cron run look successful"
        )

    only_classic = sorted(classic.keys() - mantra.keys())
    only_mantra = sorted(mantra.keys() - classic.keys())
    if only_classic or only_mantra:
        raise PoolJoinError(
            f"the two listoni disagree for season {season!r}: "
            f"{len(classic)} classic rows vs {len(mantra)} mantra rows; "
            f"only in classic: {only_classic or '-'}; only in mantra: {only_mantra or '-'}"
        )

    players = [
        PoolPlayer(
            id=player_id,
            nome=row.nome,
            squadra=row.squadra,
            # Classic carries exactly one role, stored as a one-element array.
            ruolo=row.ruoli[0] if row.ruoli else "",
            ruoli_mantra=";".join(mantra[player_id].ruoli_codice),
        )
        for player_id, row in classic.items()
    ]
    return sorted(players, key=lambda p: (p.squadra, p.nome))
