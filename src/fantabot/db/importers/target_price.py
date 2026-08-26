"""Seed ``target_price``: the auction prices the bidding logic will spend.

Two things make this file different from every other import.

**The season is not in the data.** It is in the filename, and nowhere else. Every
other CSV carries a ``stagione`` column. Deriving it here turns the filename
into a real column, which is what lets a second season's prices coexist with
this one instead of overwriting them.

**Blank means absent, and zero means zero.** Unlike ``statistiche``, which marks
no-data with ``"0,0"``, this file leaves the cell empty — 160 rows per listone
have no prior average and 363 have no prediction. A predicted delta of 0.0 is a
real prediction and must not be collapsed into NULL. That is exactly the
distinction ``plain_decimal`` draws.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fantabot.db.importers import ImportResult
from fantabot.db.importers._csv import plain_decimal, split_codes, split_flags
from fantabot.db.models.reference import TargetPrice

_FILES: tuple[tuple[str, str], ...] = (
    ("target_price_2026_27_classic.csv", "classic"),
    ("target_price_2026_27_mantra.csv", "mantra"),
)

SOURCE_FILES: tuple[str, ...] = tuple(name for name, _ in _FILES)

_SEASON_IN_FILENAME = re.compile(r"target_price_(\d{4})_(\d{2})_")

_UPDATABLE: tuple[str, ...] = (
    "squadra",
    "ruoli_codice",
    "macro_role",
    "qi",
    "prior_media_fantavoto",
    "predicted_pct_delta",
    "team_factor",
    "target_price",
    "flags",
)


class SeasonNotInFilenameError(ValueError):
    """The season could not be read from the filename, so nothing is written."""


def season_from_filename(filename: str) -> str:
    """``target_price_2026_27_classic.csv`` -> ``"2026/27"``.

    Raises rather than guessing. Writing rows under the wrong season would be
    invisible until a second season landed on top of the first.
    """
    match = _SEASON_IN_FILENAME.match(filename)
    if match is None:
        raise SeasonNotInFilenameError(
            f"cannot read a season from {filename!r}; expected target_price_YYYY_YY_*"
        )
    return f"{match.group(1)}/{match.group(2)}"


def read_rows(data_dir: Path) -> list[dict[str, Any]]:
    """Every priced player from both files, with the season filled in."""
    rows: list[dict[str, Any]] = []
    for filename, listone in _FILES:
        path = data_dir / filename
        if not path.exists():
            continue
        stagione = season_from_filename(filename)
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                raw_id = (row.get("id") or "").strip()
                if not raw_id:
                    continue
                rows.append(
                    {
                        "stagione": stagione,
                        "player_id": int(raw_id),
                        "listone": listone,
                        "squadra": row["squadra"].strip().upper(),
                        "ruoli_codice": split_codes(row["role"]),
                        "macro_role": row["macro_role"].strip(),
                        "qi": int(row["qi"]),
                        "prior_media_fantavoto": plain_decimal(row["prior_media_fantavoto"]),
                        "predicted_pct_delta": plain_decimal(row["predicted_pct_delta"]),
                        "team_factor": plain_decimal(row["team_factor"]),
                        "target_price": int(row["target_price"]),
                        "flags": split_flags(row["flags"]),
                    }
                )
    return rows


def load(session: Session, data_dir: Path) -> ImportResult:
    """Upsert every priced player. Idempotent."""
    rows = read_rows(data_dir)
    if not rows:
        return ImportResult(table="target_price")

    existing = set(
        session.execute(
            select(TargetPrice.stagione, TargetPrice.player_id, TargetPrice.listone)
        ).all()
    )
    keys = {(row["stagione"], row["player_id"], row["listone"]) for row in rows}
    inserted = len(keys - existing)

    statement = insert(TargetPrice).values(rows)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[TargetPrice.stagione, TargetPrice.player_id, TargetPrice.listone],
            set_={column: statement.excluded[column] for column in _UPDATABLE},
        )
    )
    return ImportResult(
        table="target_price", inserted=inserted, unchanged=len(rows) - inserted
    )
