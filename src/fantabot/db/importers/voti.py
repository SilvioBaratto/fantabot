"""Seed ``voti``: one row per player per matchday, three grading sources wide.

The largest table in the project. Comma-decimal, so the six grade columns go
through ``italian_decimal``.

Coach rows — 3039 of them — have an empty id and land with ``player_id`` NULL,
covered by the second partial unique index rather than rejected. 88 of the
non-coach rows reference a player who exists only because ``players`` was seeded
from the union of every id source rather than from quotazioni alone.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fantabot.db.importers import ImportResult
from fantabot.db.importers._csv import italian_decimal
from fantabot.db.importers.matches import parse_date, parse_time, upsert_two_passes
from fantabot.db.models.matches import Voto

SOURCE_FILES: tuple[str, ...] = ("voti.csv",)

_GRADES: tuple[str, ...] = (
    "voto_fc",
    "fantavoto_fc",
    "voto_stat",
    "fantavoto_stat",
    "voto_italia",
    "fantavoto_italia",
)

_UPDATABLE: tuple[str, ...] = (
    "data",
    "ora",
    "squadra_raw",
    "avversario_raw",
    "gol_squadra",
    "gol_avversario",
    "nome",
    "ruolo_codice",
    "ruolo",
    *_GRADES,
)


def read_rows(data_dir: Path) -> list[dict[str, Any]]:
    """Every graded appearance, coach rows included."""
    path = data_dir / SOURCE_FILES[0]
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_id = (row.get("id") or "").strip()
            record: dict[str, Any] = {
                "stagione": row["stagione"].strip(),
                "giornata": int(row["giornata"]),
                "data": parse_date(row["data"]),
                "ora": parse_time(row.get("ora", "")),
                "squadra_raw": row["squadra"].strip(),
                "avversario_raw": row["avversario"].strip(),
                "gol_squadra": int(row["gol_squadra"]),
                "gol_avversario": int(row["gol_avversario"]),
                "player_id": int(raw_id) if raw_id else None,
                "nome": row["nome"].strip(),
                "ruolo_codice": row["ruolo_codice"].strip().upper(),
                "ruolo": row["ruolo"].strip(),
            }
            record.update({name: italian_decimal(row[name]) for name in _GRADES})
            rows.append(record)
    return rows


def load(session: Session, data_dir: Path) -> ImportResult:
    """Upsert every graded appearance. Idempotent."""
    rows = read_rows(data_dir)
    if not rows:
        return ImportResult(table="voti")

    before = session.execute(select(func.count()).select_from(Voto)).scalar() or 0
    upsert_two_passes(session, Voto, rows, updatable=_UPDATABLE)
    session.flush()
    after = session.execute(select(func.count()).select_from(Voto)).scalar() or 0

    inserted = after - before
    return ImportResult(table="voti", inserted=inserted, unchanged=len(rows) - inserted)
