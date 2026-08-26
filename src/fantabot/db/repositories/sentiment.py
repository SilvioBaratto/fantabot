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

from sqlalchemy import cast, desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.types import Text

from fantabot.data_sources.news_sentiment import RoleDrift, SentimentRow, TrailingSentiment
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


def _to_row(record: PlayerSentiment) -> SentimentRow:
    """One stored reading as the value type callers already expect.

    Scores come back as ``float``, not ``Decimal``. ``SentimentRow`` is annotated
    ``float`` and ``trailing`` averages them; a ``Decimal`` would type-check
    against ``Any`` and then raise the moment it met a float in arithmetic.
    """
    return SentimentRow(
        player_id=str(record.player_id),
        nome=record.nome,
        data_run=record.data_run.isoformat(),
        ruolo_campo=record.ruolo_campo,
        ruoli_mantra=record.ruoli_mantra,
        deriva_ruolo=float(record.deriva_ruolo),
        **{name: float(getattr(record, name)) for name in SCORE_COLUMNS},
    )


class SentimentReadRepository(RepositoryBase):
    """The queries ``NewsSentimentSource`` serves, as SQL.

    Four behaviours here that the natural translation quietly breaks, all of
    them observable:

    * **Explicit ordering.** The CSV version sorts by ISO date string on load,
      and ``latest`` is the last element. Postgres has no inherent row order, so
      every query orders by ``data_run`` explicitly. Omit it and ``latest``
      returns an arbitrary row — and passes on a small fixture.
    * **Slice, then filter.** ``trailing`` takes the last ``weeks`` rows and
      *then* drops the silent ones, so a window of four runs where two were
      silent reports ``rows_used == 2``. Filtering first would reach further
      back and silently widen the window.
    * **All-silent is ``None``.** ``confidenza == 0`` means no coverage was
      found, not that the player is neutral. A player whose whole window is
      silent has no average, rather than an average of zero.
    * **String tie-break.** ``drifted`` breaks ties on ``player_id`` compared as
      text, because the CSV version held it as ``str``. Arbitrary, but it is the
      existing order and changing it silently would reshuffle the list.
    """

    def latest(self, player_id: str) -> SentimentRow | None:
        record = self.session.execute(
            select(PlayerSentiment)
            .where(PlayerSentiment.player_id == int(player_id))
            .order_by(desc(PlayerSentiment.data_run))
            .limit(1)
        ).scalar_one_or_none()
        return None if record is None else _to_row(record)

    def trailing(self, player_id: str, weeks: int = 4) -> TrailingSentiment | None:
        """Mean of each score over the last ``weeks`` runs, silent rows excluded."""
        records = self.session.execute(
            select(PlayerSentiment)
            .where(PlayerSentiment.player_id == int(player_id))
            .order_by(desc(PlayerSentiment.data_run))
            .limit(weeks)
        ).scalars().all()

        window = [_to_row(record) for record in records if record.confidenza > 0]
        if not window:
            return None

        def mean(name: str) -> float:
            return sum(float(getattr(row, name)) for row in window) / len(window)

        return TrailingSentiment(
            player_id=player_id,
            rows_used=len(window),
            sentiment=mean("sentiment"),
            disponibilita=mean("disponibilita"),
            titolarita=mean("titolarita"),
            mercato=mean("mercato"),
            forma=mean("forma"),
            rigorista=mean("rigorista"),
            piazzati=mean("piazzati"),
        )

    def drift(self, player_id: str) -> RoleDrift | None:
        """The latest role drift for one player, or ``None`` if the tag holds."""
        row = self.latest(player_id)
        if row is None or row.deriva_ruolo <= 0:
            return None
        return RoleDrift(
            player_id=row.player_id,
            nome=row.nome,
            ruoli_mantra=row.ruoli_mantra,
            ruolo_campo=row.ruolo_campo,
            deriva_ruolo=row.deriva_ruolo,
        )

    def drifted(self) -> list[RoleDrift]:
        """Every player whose frozen Mantra tag no longer describes them.

        One statement, not one per player. ``DISTINCT ON`` takes each player's
        most recent reading; the outer query keeps the ones that drifted and
        orders them worst first.
        """
        latest_per_player = (
            select(
                PlayerSentiment.player_id,
                PlayerSentiment.nome,
                PlayerSentiment.ruoli_mantra,
                PlayerSentiment.ruolo_campo,
                PlayerSentiment.deriva_ruolo,
            )
            .distinct(PlayerSentiment.player_id)
            .order_by(PlayerSentiment.player_id, desc(PlayerSentiment.data_run))
            .subquery()
        )

        rows = self.session.execute(
            select(latest_per_player)
            .where(latest_per_player.c.deriva_ruolo > 0)
            .order_by(
                desc(latest_per_player.c.deriva_ruolo),
                cast(latest_per_player.c.player_id, Text),
            )
        ).all()

        return [
            RoleDrift(
                player_id=str(row.player_id),
                nome=row.nome,
                ruoli_mantra=row.ruoli_mantra,
                ruolo_campo=row.ruolo_campo,
                deriva_ruolo=float(row.deriva_ruolo),
            )
            for row in rows
        ]
