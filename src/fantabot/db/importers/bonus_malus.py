"""Seed ``bonus_malus``: the countable events behind each fantavoto.

Same grain, same keying problem and the same 3039 coach rows as ``voti``, so it
reuses ``upsert_two_passes`` rather than restating the two-conflict-target
mechanic. The files agree exactly: 50,634 rows each, identical coach rows, and
neither partial key has a duplicate.

The ten counters are plain integers and NOT NULL — a player who scored no goals
scored zero goals, which is a fact rather than a gap. There is no ``ora`` column
in this file and no goal columns for the fixture.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fantabot.db.importers import ImportResult
from fantabot.db.importers.matches import parse_date, upsert_two_passes
from fantabot.db.models.matches import BonusMalus

SOURCE_FILES: tuple[str, ...] = ("bonus_malus.csv",)

_COUNTERS: tuple[str, ...] = (
    "ammonizione",
    "espulsione",
    "gol_segnati",
    "gol_subiti",
    "autoreti",
    "rigori_segnati",
    "rigori_sbagliati",
    "rigori_parati",
    "assist",
    "mvp",
)

_UPDATABLE: tuple[str, ...] = (
    "data",
    "squadra_raw",
    "avversario_raw",
    "nome",
    "ruolo_codice",
    "ruolo",
    *_COUNTERS,
)


def read_rows(data_dir: Path) -> list[dict[str, Any]]:
    """Every event row, coach rows included."""
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
                "squadra_raw": row["squadra"].strip(),
                "avversario_raw": row["avversario"].strip(),
                "player_id": int(raw_id) if raw_id else None,
                "nome": row["nome"].strip(),
                "ruolo_codice": row["ruolo_codice"].strip().upper(),
                "ruolo": row["ruolo"].strip(),
            }
            record.update({name: int(row[name]) for name in _COUNTERS})
            rows.append(record)
    return rows


def load(session: Session, data_dir: Path) -> ImportResult:
    """Upsert every event row. Idempotent."""
    rows = read_rows(data_dir)
    if not rows:
        return ImportResult(table="bonus_malus")

    before = session.execute(select(func.count()).select_from(BonusMalus)).scalar() or 0
    upsert_two_passes(session, BonusMalus, rows, updatable=_UPDATABLE)
    session.flush()
    after = session.execute(select(func.count()).select_from(BonusMalus)).scalar() or 0

    inserted = after - before
    return ImportResult(
        table="bonus_malus", inserted=inserted, unchanged=len(rows) - inserted
    )
