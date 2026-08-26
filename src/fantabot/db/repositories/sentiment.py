"""The weekly sentiment time series: writing it, and reading it back.

The write path replaces a full-file rescan with a primary key. ``existing_keys``
returns ``(data_run, player_id)`` as **strings**, deliberately: ``cli.py`` builds
``(today.isoformat(), p.id)`` to compare against, and a repository that returned
``(date, int)`` would match nothing — every one of the 523 players would be
re-queried and the run would still report success. That shape is pinned by
``tests/test_news_store_contract.py``.

``--force`` is a **behaviour change**, not a port. Today it merely skips the
resume filter, and since ``append_rows`` has no dedup it writes a second row for
the same key which ``_load`` then keeps. Here it becomes ``DO UPDATE``: one row
per key, always.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from fantabot.db.models.sentiment import SCORE_COLUMNS, PlayerSentiment
from fantabot.db.repositories._base import RepositoryBase

_TEXT_COLUMNS: tuple[str, ...] = (
    "stagione",
    "nome",
    "squadra",
    "ruolo",
    "ruoli_mantra",
    "ruolo_campo",
    "riassunto",
    "modello",
)

_UPDATABLE: tuple[str, ...] = (
    "giorni_lookback",
    *_TEXT_COLUMNS,
    "deriva_ruolo",
    *SCORE_COLUMNS,
    "n_fonti",
    "fonti",
)


def to_record(row: Mapping[str, str]) -> dict[str, Any]:
    """Turn one ``store.build_row`` output into typed column values. Pure.

    ``build_row`` produces all-string cells because its target was a CSV. The
    conversion lives here rather than in ``build_row`` so that function stays
    pure and its eleven existing tests keep describing the same thing.
    """
    record: dict[str, Any] = {
        "data_run": datetime.strptime(row["data_run"], "%Y-%m-%d").date(),
        "player_id": int(row["id"]),
        "giorni_lookback": int(row["giorni_lookback"]),
        "deriva_ruolo": Decimal(row["deriva_ruolo"]),
        "n_fonti": int(row["n_fonti"]),
        # ";"-joined on the way in, text[] in the table.
        "fonti": [part for part in row["fonti"].split(";") if part],
    }
    record.update({name: row[name] for name in _TEXT_COLUMNS})
    record.update({name: Decimal(row[name]) for name in SCORE_COLUMNS})
    return record


class SentimentRepository(RepositoryBase):
    """Everything the news pipeline and the strategy layer ask of this table."""

    def existing_keys(self, data_run: date) -> set[tuple[str, str]]:
        """``(data_run, player_id)`` already stored, as strings.

        Strings because that is what ``cli.py`` compares against. Returning the
        native types here would silently disable resume.
        """
        rows = self.session.execute(
            select(PlayerSentiment.data_run, PlayerSentiment.player_id).where(
                PlayerSentiment.data_run == data_run
            )
        ).all()
        return {(stored.isoformat(), str(player_id)) for stored, player_id in rows}

    def upsert_rows(
        self, rows: Sequence[Mapping[str, str]], *, force: bool = False
    ) -> int:
        """Insert new readings; with ``force``, overwrite existing ones.

        Returns the number of rows sent. An empty batch issues no statement and
        opens no transaction, matching ``store.append_rows``, which returns
        before touching the filesystem — so a run that produced nothing does not
        create an empty artefact.
        """
        if not rows:
            return 0

        records = [to_record(row) for row in rows]
        statement = insert(PlayerSentiment).values(records)

        if force:
            statement = statement.on_conflict_do_update(
                index_elements=[PlayerSentiment.data_run, PlayerSentiment.player_id],
                set_={column: statement.excluded[column] for column in _UPDATABLE},
            )
        else:
            statement = statement.on_conflict_do_nothing(
                index_elements=[PlayerSentiment.data_run, PlayerSentiment.player_id]
            )

        self.session.execute(statement)
        return len(records)
