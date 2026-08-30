"""Seed ``teams``: the bridge between the two team vocabularies.

``quotazioni``/``statistiche``/``qi_bias``/``target_price`` identify a club by a
three-letter code; ``voti``/``bonus_malus`` use the full name. Nothing in the
data states the correspondence, so it is derived — and then **gated**, because a
wrong or partial mapping does not fail loudly. It makes later joins return zero
rows while every table still looks populated.

The rule is that the code is the name's first three letters, upper-cased.
Verified against the files on disk: 27 codes, 27 full names, the mapping is a
bijection with no prefix collisions and nothing unresolved in either direction.

A caveat that matters and is easy to misread: ``voti.squadra`` is corrupt
per-row — the scraper labels every row in a match block with the fixture's *home*
team, so the column cannot say which side a player played for. The full statement
of that bug lives on ``db/models/matches.py``. The *set* of names it contains is
still complete and correct, which is all this importer reads from it.
"""

from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fantabot.club_names import build_mapping
from fantabot.db.importers import ImportResult
from fantabot.db.models.reference import Team

_CODE_SOURCES: tuple[str, ...] = ("quotazioni_classic.csv", "quotazioni_mantra.csv")
_NAME_SOURCES: tuple[str, ...] = ("voti.csv", "bonus_malus.csv")

SOURCE_FILES: tuple[str, ...] = _CODE_SOURCES + _NAME_SOURCES

# The mapping itself is pure and outlives this importer — it is fed from
# Postgres now, not from these files.



def read_season_codes(data_dir: Path) -> set[tuple[str, str]]:
    """Every ``(stagione, codice)`` pair the listoni declare."""
    pairs: set[tuple[str, str]] = set()
    for filename in _CODE_SOURCES:
        path = data_dir / filename
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                stagione = (row.get("stagione") or "").strip()
                codice = (row.get("squadra") or "").strip().upper()
                if stagione and codice:
                    pairs.add((stagione, codice))
    return pairs


def read_full_names(data_dir: Path) -> set[str]:
    """Every full club name the match-grain files mention, from both columns."""
    nomi: set[str] = set()
    for filename in _NAME_SOURCES:
        path = data_dir / filename
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                for column in ("squadra", "avversario"):
                    value = (row.get(column) or "").strip()
                    if value:
                        nomi.add(value)
    return nomi


def load(session: Session, data_dir: Path) -> ImportResult:
    """Upsert one row per (season, club). Raises before writing if the map is bad."""
    pairs = read_season_codes(data_dir)
    if not pairs:
        return ImportResult(table="teams")

    mapping = build_mapping(read_full_names(data_dir), {code for _, code in pairs})

    existing = set(
        session.execute(select(Team.stagione, Team.codice)).all()
    )
    rows = [
        {"stagione": stagione, "codice": codice, "nome_completo": mapping[codice]}
        for stagione, codice in sorted(pairs)
    ]
    inserted = len(pairs - existing)

    statement = insert(Team).values(rows)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[Team.stagione, Team.codice],
            set_={"nome_completo": statement.excluded.nome_completo},
        )
    )
    return ImportResult(table="teams", inserted=inserted, unchanged=len(rows) - inserted)
