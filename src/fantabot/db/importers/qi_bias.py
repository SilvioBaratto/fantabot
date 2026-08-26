"""Seed ``qi_bias``: the gap between initial quote and actual auction price.

**Dot-decimal, not comma-decimal.** ``qi_bias_*.csv`` writes ``38.46`` where
``statistiche`` would write ``"38,46"``, so this importer uses ``plain_decimal``.
Using ``italian_decimal`` here would raise on every row — which is the point of
having two parsers: the alternative, a single forgiving one, would have read
``38.46`` as ``3846`` and nothing would have complained.

``role`` is lower-cased in the Classic file (``a``/``c``/``d``/``p``) and
``;``-joined uppercase in the Mantra one, so it goes through ``split_codes``,
which normalises both to the same convention ``quotazioni`` uses.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fantabot.db.importers import ImportResult
from fantabot.db.importers._csv import plain_decimal, split_codes
from fantabot.db.models.reference import QiBias

_FILES: tuple[tuple[str, str], ...] = (
    ("qi_bias_classic.csv", "classic"),
    ("qi_bias_mantra.csv", "mantra"),
)

SOURCE_FILES: tuple[str, ...] = tuple(name for name, _ in _FILES)

_UPDATABLE: tuple[str, ...] = ("squadra", "ruoli_codice", "qi", "qa", "fvm", "delta", "pct_delta")


def read_rows(data_dir: Path) -> list[dict[str, Any]]:
    """Every bias row from both files."""
    rows: list[dict[str, Any]] = []
    for filename, listone in _FILES:
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
                        "stagione": row["stagione"].strip(),
                        "player_id": int(raw_id),
                        "listone": listone,
                        "squadra": row["squadra"].strip().upper(),
                        "ruoli_codice": split_codes(row["role"]),
                        "qi": int(row["qi"]),
                        "qa": int(row["qa"]),
                        "fvm": int(row["fvm"]),
                        "delta": int(row["delta"]),
                        "pct_delta": plain_decimal(row["pct_delta"]),
                    }
                )
    return rows


def load(session: Session, data_dir: Path) -> ImportResult:
    """Upsert every bias row. Idempotent."""
    rows = read_rows(data_dir)
    if not rows:
        return ImportResult(table="qi_bias")

    existing = set(
        session.execute(select(QiBias.stagione, QiBias.player_id, QiBias.listone)).all()
    )
    keys = {(row["stagione"], row["player_id"], row["listone"]) for row in rows}
    inserted = len(keys - existing)

    statement = insert(QiBias).values(rows)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[QiBias.stagione, QiBias.player_id, QiBias.listone],
            set_={column: statement.excluded[column] for column in _UPDATABLE},
        )
    )
    return ImportResult(table="qi_bias", inserted=inserted, unchanged=len(rows) - inserted)
