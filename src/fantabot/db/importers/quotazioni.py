"""Seed ``quotazioni`` from the two listone files into one table.

The files differ only in the role column — ``ruolo_codice``/``ruolo`` in Classic,
``ruoli_codice``/``ruoli`` in Mantra — so they share a table with a ``listone``
discriminator and both role columns stored as ``text[]``. Classic's arrays hold
exactly one element.

This is also the table that resolves a player's real club for a season, which
every later workaround for ``voti.squadra`` being corrupt depends on.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fantabot.db.importers import ImportResult
from fantabot.db.importers._csv import split_codes, split_flags
from fantabot.db.models.reference import Quotazione

# filename, listone, code column, label column
_FILES: tuple[tuple[str, str, str, str], ...] = (
    ("quotazioni_classic.csv", "classic", "ruolo_codice", "ruolo"),
    ("quotazioni_mantra.csv", "mantra", "ruoli_codice", "ruoli"),
)

SOURCE_FILES: tuple[str, ...] = tuple(name for name, *_ in _FILES)


def read_rows(data_dir: Path) -> list[dict[str, Any]]:
    """Every valuation row from both files, ready to insert."""
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
                rows.append(
                    {
                        "stagione": (row["stagione"]).strip(),
                        "player_id": int(raw_id),
                        "listone": listone,
                        "squadra": row["squadra"].strip().upper(),
                        "ruoli_codice": split_codes(row[code_column]),
                        # Labels, not codes: "Attaccante" must not become "ATTACCANTE".
                        "ruoli": split_flags(row[label_column]),
                        "qi": int(row["qi"]),
                        "qa": int(row["qa"]),
                        "fvm": int(row["fvm"]),
                    }
                )
    return rows


def load(session: Session, data_dir: Path) -> ImportResult:
    """Upsert every valuation. Idempotent."""
    rows = read_rows(data_dir)
    if not rows:
        return ImportResult(table="quotazioni")

    existing = set(
        session.execute(
            select(Quotazione.stagione, Quotazione.player_id, Quotazione.listone)
        ).all()
    )
    keys = {(row["stagione"], row["player_id"], row["listone"]) for row in rows}
    inserted = len(keys - existing)

    statement = insert(Quotazione).values(rows)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[Quotazione.stagione, Quotazione.player_id, Quotazione.listone],
            set_={
                column: statement.excluded[column]
                for column in ("squadra", "ruoli_codice", "ruoli", "qi", "qa", "fvm")
            },
        )
    )
    return ImportResult(
        table="quotazioni", inserted=inserted, unchanged=len(rows) - inserted
    )
