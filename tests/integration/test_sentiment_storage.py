"""The storage contracts the CSV suite used to pin, now against the table.

Each test here replaces one from ``tests/test_news_store.py``'s append_rows and
existing_keys sections. The mechanics changed completely — a primary key instead
of a rescan, a transaction instead of an append — so the tests are rewritten
rather than ported. What is preserved is what each one was actually protecting.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.repositories.sentiment import SentimentRepository

pytestmark = pytest.mark.db


def _row(player_id: str, data_run: str = "2026-10-07", **overrides: str) -> dict[str, str]:
    row = {
        "data_run": data_run,
        "giorni_lookback": "14",
        "stagione": "2026/27",
        "id": player_id,
        "nome": "Fixture",
        "squadra": "ATA",
        "ruolo": "Difensore",
        "ruoli_mantra": "B;DS",
        "ruolo_campo": "B",
        "deriva_ruolo": "0.00",
        "sentiment": "0.50",
        "disponibilita": "1.00",
        "titolarita": "0.90",
        "mercato": "0.00",
        "forma": "0.00",
        "rigorista": "0.80",
        "piazzati": "0.10",
        "confidenza": "0.80",
        "riassunto": "x",
        "n_fonti": "2",
        "fonti": "https://a;https://b",
        "modello": "test",
    }
    row.update(overrides)
    return row


@pytest.fixture
def two_players(synthetic_players: Any) -> list[str]:
    """Synthetic, not borrowed.

    This used to be ``SELECT id FROM players ORDER BY id LIMIT 2`` — real players, with real
    readings. Once the table held a full listone those readings collided with the fixture's
    own on ``(data_run, player_id)``, and the round-trip assertions started reading back
    somebody else's prose.
    """
    return synthetic_players(2)


def test_a_second_write_preserves_the_first(
    db_session: Session, two_players: list[str]
) -> None:
    """Was: appending preserves prior rows. The history is the whole point —
    a past Wednesday cannot be regenerated."""
    first, second = two_players
    repo = SentimentRepository(db_session)

    repo.upsert_rows([_row(first)])
    repo.upsert_rows([_row(second)])

    stored = db_session.execute(
        text("SELECT count(*) FROM player_sentiment WHERE data_run = '2026-10-07'")
    ).scalar()
    assert stored == 2


def test_awkward_prose_round_trips(db_session: Session, two_players: list[str]) -> None:
    """Was: commas, quotes and newlines round-trip through the CSV writer.

    Trivially true of a text column, which is exactly why it is worth stating:
    the escaping problem the CSV had is gone rather than merely handled.
    """
    player_id = two_players[0]
    awkward = 'Fuori 3 settimane, "problema muscolare"\nrientro previsto: 20/10'

    SentimentRepository(db_session).upsert_rows([_row(player_id, riassunto=awkward)])

    stored = db_session.execute(
        text("SELECT riassunto FROM player_sentiment WHERE player_id = :p"),
        {"p": int(player_id)},
    ).scalar()
    assert stored == awkward


def test_an_empty_batch_writes_nothing(
    db_session: Session, two_players: list[str]
) -> None:
    """Was: appending nothing does not create a file."""
    before = db_session.execute(text("SELECT count(*) FROM player_sentiment")).scalar()

    SentimentRepository(db_session).upsert_rows([])

    after = db_session.execute(text("SELECT count(*) FROM player_sentiment")).scalar()
    assert after == before


def test_the_resume_index_on_an_empty_day_is_empty(db_session: Session) -> None:
    """Was: existing_keys on a missing file is empty."""
    keys = SentimentRepository(db_session).existing_keys(date(2099, 1, 1))

    assert keys == set()


def test_the_resume_index_covers_every_player_written_that_day(
    db_session: Session, two_players: list[str]
) -> None:
    """Was: existing_keys indexes by run and player."""
    first, second = two_players
    repo = SentimentRepository(db_session)
    repo.upsert_rows([_row(first), _row(second)])

    keys = repo.existing_keys(date(2026, 10, 7))

    assert {("2026-10-07", first), ("2026-10-07", second)} <= keys


def test_a_row_from_another_run_day_does_not_block_today(
    db_session: Session, two_players: list[str]
) -> None:
    """Was: a player from another run day does not block today.

    The one with teeth. A resume index that ignored the date would skip every
    player who had ever been queried, and the weekly run would do nothing while
    reporting success.
    """
    player_id = two_players[0]
    repo = SentimentRepository(db_session)
    repo.upsert_rows([_row(player_id, data_run="2026-10-01")])

    keys = repo.existing_keys(date(2026, 10, 7))

    assert ("2026-10-07", player_id) not in keys
