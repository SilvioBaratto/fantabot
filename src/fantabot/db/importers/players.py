"""Seed ``players`` from the union of every id source. Not from quotazioni alone.

**This is the ordering fact the whole import depends on**, and it is easy to get
wrong because the obvious source is nearly right. Measured on the data:

    quotazioni_classic / quotazioni_mantra   1414 ids, identical sets
    statistiche_classic / statistiche_mantra 1334 ids, all within quotazioni
    target_price_2026_27_*                    523 ids, all within quotazioni
    voti / bonus_malus                       1224 ids, 60 of them NOWHERE else
    ------------------------------------------------------------------
    union                                    1474

Seeding from quotazioni alone gives 1414 and looks fine until ``voti`` loads:
88 rows per file reference one of those 60 ids and violate the foreign key. The
60 are players who appeared in a match in some season but are absent from every
listone — transfers away, short loans, players who never got a quotazione.

Name resolution is deterministic because 94 ids carry more than one spelling
across seasons. The most recent season wins; ties break toward the more
canonical source, quotazioni first.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fantabot.db.importers import ImportResult
from fantabot.db.models.reference import Player

# Lower rank is more canonical. Only used to break a same-season tie.
_SOURCES: tuple[tuple[str, int], ...] = (
    ("quotazioni_classic.csv", 0),
    ("quotazioni_mantra.csv", 0),
    ("statistiche_classic.csv", 1),
    ("statistiche_mantra.csv", 1),
    ("voti.csv", 2),
    ("bonus_malus.csv", 3),
)

SOURCE_FILES: tuple[str, ...] = tuple(name for name, _ in _SOURCES)


@dataclass(frozen=True)
class PlayerRef:
    """One (id, name) sighting, with enough context to rank it."""

    player_id: int
    nome: str
    stagione: str
    source_rank: int


def resolve_names(refs: Iterable[PlayerRef]) -> dict[int, str]:
    """Collapse every sighting into one name per id. Pure.

    Most recent season wins; within a season the lower ``source_rank`` wins.
    Deterministic regardless of the order refs arrive in, which matters because
    a dict that depended on file order would make the seed unreproducible.
    """
    best: dict[int, tuple[str, int]] = {}
    chosen: dict[int, str] = {}
    for ref in refs:
        key = (ref.stagione, -ref.source_rank)
        if ref.player_id not in best or key > best[ref.player_id]:
            best[ref.player_id] = key
            chosen[ref.player_id] = ref.nome
    return chosen


def read_refs(data_dir: Path) -> Iterator[PlayerRef]:
    """Every (id, name, season) sighting across the six files that carry them."""
    for filename, rank in _SOURCES:
        path = data_dir / filename
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                raw_id = (row.get("id") or "").strip()
                if not raw_id:
                    # Coach rows: 3039 per match-grain file, no player id.
                    continue
                nome = (row.get("nome") or "").strip()
                if not nome:
                    continue
                yield PlayerRef(
                    player_id=int(raw_id),
                    nome=nome,
                    stagione=(row.get("stagione") or "").strip(),
                    source_rank=rank,
                )


def load(session: Session, data_dir: Path) -> ImportResult:
    """Upsert every player. Idempotent: a re-run inserts nothing."""
    names = resolve_names(read_refs(data_dir))
    if not names:
        return ImportResult(table="players")

    existing = set(session.execute(select(Player.id)).scalars())
    inserted = len(set(names) - existing)

    statement = insert(Player).values(
        [{"id": player_id, "nome": nome} for player_id, nome in sorted(names.items())]
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[Player.id], set_={"nome": statement.excluded.nome}
        )
    )
    return ImportResult(
        table="players", inserted=inserted, unchanged=len(names) - inserted
    )
