"""Seed ``statistiche``: season totals per listone, per grading source.

Comma-decimal territory. ``media_voto`` and ``media_fantavoto`` go through
``italian_decimal``, which maps the ``"0,0"`` no-data marker to ``None`` — 2846
rows per listone carry it, and SPEC criterion 9 requires them to arrive as NULL
and never as zero.

The counter columns are plain integers and really are zero when they say zero,
so they are read with ``int`` and stored NOT NULL.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fantabot.db.importers import ImportResult
from fantabot.db.importers._csv import italian_decimal, split_codes
from fantabot.db.models.reference import Statistica

_FILES: tuple[tuple[str, str, str, str], ...] = (
    ("statistiche_classic.csv", "classic", "ruolo_codice", "ruolo"),
    ("statistiche_mantra.csv", "mantra", "ruoli_codice", "ruoli"),
)

SOURCE_FILES: tuple[str, ...] = tuple(name for name, *_ in _FILES)

_COUNTERS: tuple[str, ...] = (
    "partite_giocate",
    "gol",
    "gol_subiti",
    "rigori_segnati",
    "rigori_tirati",
    "rigori_parati",
    "assist",
    "ammonizioni",
    "espulsioni",
)

_UPDATABLE: tuple[str, ...] = (
    "squadra",
    "ruoli_codice",
    "ruoli",
    "media_voto",
    "media_fantavoto",
    *_COUNTERS,
)


def read_rows(data_dir: Path) -> list[dict[str, Any]]:
    """Every season-total row from both files."""
    rows: list[dict[str, Any]] = []
    for filename, listone, code_column, label_column in _FILES:
        path = data_dir / filename
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                raw_id = (row.get("id") or "").strip()
                if not raw_id:
                    continue
                record: dict[str, Any] = {
                    "stagione": row["stagione"].strip(),
                    "fonte": row["fonte"].strip(),
                    "player_id": int(raw_id),
                    "listone": listone,
                    "squadra": row["squadra"].strip().upper(),
                    "ruoli_codice": split_codes(row[code_column]),
                    "ruoli": split_codes(row[label_column]),
                    "media_voto": italian_decimal(row["media_voto"]),
                    "media_fantavoto": italian_decimal(row["media_fantavoto"]),
                }
                record.update({name: int(row[name]) for name in _COUNTERS})
                rows.append(record)
    return rows


def load(session: Session, data_dir: Path) -> ImportResult:
    """Upsert every season total. Idempotent."""
    rows = read_rows(data_dir)
    if not rows:
        return ImportResult(table="statistiche")

    existing = set(
        session.execute(
            select(
                Statistica.stagione,
                Statistica.fonte,
                Statistica.player_id,
                Statistica.listone,
            )
        ).all()
    )
    keys = {
        (row["stagione"], row["fonte"], row["player_id"], row["listone"]) for row in rows
    }
    inserted = len(keys - existing)

    statement = insert(Statistica).values(rows)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                Statistica.stagione,
                Statistica.fonte,
                Statistica.player_id,
                Statistica.listone,
            ],
            set_={column: statement.excluded[column] for column in _UPDATABLE},
        )
    )
    return ImportResult(
        table="statistiche", inserted=inserted, unchanged=len(rows) - inserted
    )
