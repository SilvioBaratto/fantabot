"""SPEC's headline definition of done, as one runnable command.

    docker compose up -d && alembic upgrade head && fantabot db-import --all

...and every number that was in a CSV is in a table, verified by row counts
measured from the files on disk rather than copied from a plan.

Covers success criteria 6, 7, 8 and 9 together, plus the column-coverage check
that row counts alone cannot give: a dropped column changes no count, so every
header column must be accounted for by name.

This module runs the **real** import against the real database rather than the
rolled-back session fixture, because that is the thing being tested. It is safe
to repeat: the import is idempotent, which is criterion 7.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from fantabot.config import settings
from fantabot.db import database_manager, importers

pytestmark = pytest.mark.db

DATA_DIR = Path(settings.fantabot_data_dir)

# Measured from the files, not taken from the plan. These are FLOORS, not
# equalities: the scrapers read the live site, and the site moves. On 2026-08-26
# a scrape added 21 players to the 2026/27 listone that the CSVs never had.
# Asserting equality here would pin a snapshot; SC 6 asks that every number that
# was in a CSV is in a table, which is a subset relation.
MINIMUM_ROWS: dict[str, int] = {
    "players": 1474,
    "teams": 100,
    "quotazioni": 6402,
    "statistiche": 16068,
    "qi_bias": 5356,
    "target_price": 1046,
    "voti": 50634,
    "bonus_malus": 50634,
}

# Every source column is either a table column, renamed, or deliberately dropped.
RENAMED: dict[str, str] = {
    "id": "player_id",
    "squadra": "squadra_raw",
    "avversario": "avversario_raw",
    "role": "ruoli_codice",
    "ruolo_codice": "ruoli_codice",
    "ruoli_codice": "ruoli_codice",
    # Classic writes the singular label, Mantra the ";"-joined plural. Both
    # land in the same text[] column, which is why one table serves both.
    "ruolo": "ruoli",
    "ruoli": "ruoli",
}

# Dropped on purpose, with the reason.
DROPPED: dict[str, str] = {
    # The display name lives once, in players. Repeating it per season would
    # let the two disagree.
    "nome": "carried by players",
}

SOURCE_TO_TABLE: dict[str, str] = {
    "quotazioni_classic.csv": "quotazioni",
    "quotazioni_mantra.csv": "quotazioni",
    "statistiche_classic.csv": "statistiche",
    "statistiche_mantra.csv": "statistiche",
    "qi_bias_classic.csv": "qi_bias",
    "qi_bias_mantra.csv": "qi_bias",
    "target_price_2026_27_classic.csv": "target_price",
    "target_price_2026_27_mantra.csv": "target_price",
    "voti.csv": "voti",
    "bonus_malus.csv": "bonus_malus",
}

# voti and bonus_malus keep nome: coach rows have no player id, so it is the
# only thing identifying them.
_KEEPS_NOME = {"voti", "bonus_malus"}


def _skip_without_sources() -> None:
    missing = [name for name in SOURCE_TO_TABLE if not (DATA_DIR / name).exists()]
    if missing:
        pytest.skip(f"source CSVs absent: {', '.join(missing)}")


@pytest.fixture(scope="module")
def seeded() -> Engine:
    """Run the real import twice, so idempotence is the precondition of the rest."""
    _skip_without_sources()

    for _ in range(2):
        for importer in importers.REGISTRY:
            with database_manager.get_session() as session:
                importer.load(session, DATA_DIR)

    engine = database_manager.engine
    assert engine is not None
    return engine


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        value = connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar()
    return int(value or 0)


@pytest.mark.parametrize(("table", "minimum"), sorted(MINIMUM_ROWS.items()))
def test_every_table_holds_at_least_the_rows_its_source_file_had(
    seeded: Engine, table: str, minimum: int
) -> None:
    """Criterion 6, and criterion 7 by construction — the fixture already ran
    the import twice, so these counts are post-second-run.

    At least, not exactly: a scraper run legitimately adds rows the CSVs never
    had. What must never happen is a row going missing.
    """
    assert _count(seeded, table) >= minimum


def test_the_coach_rows_survived_both_match_files(seeded: Engine) -> None:
    """Criterion 8. A NOT NULL foreign key would have rejected 3039 rows per
    file, and the totals above would still have looked plausible."""
    with seeded.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM voti WHERE player_id IS NULL), "
                "(SELECT count(*) FROM bonus_malus WHERE player_id IS NULL)"
            )
        ).one()
    assert counts == (3039, 3039)


def test_the_no_data_marker_never_became_a_zero(seeded: Engine) -> None:
    """Criterion 9. 2846 absent averages must be NULL, and none may be 0."""
    with seeded.connect() as connection:
        zeros, nulls = connection.execute(
            text(
                "SELECT count(*) FILTER (WHERE media_voto = 0), "
                "count(*) FILTER (WHERE media_voto IS NULL) FROM statistiche"
            )
        ).one()
    assert (zeros, nulls) == (0, 2846)


def test_nothing_is_orphaned_anywhere(seeded: Engine) -> None:
    """Every foreign key across the schema, in one query."""
    with seeded.connect() as connection:
        orphans = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM quotazioni q "
                "  LEFT JOIN players p ON p.id = q.player_id WHERE p.id IS NULL) "
                "+ (SELECT count(*) FROM statistiche s "
                "  LEFT JOIN players p ON p.id = s.player_id WHERE p.id IS NULL) "
                "+ (SELECT count(*) FROM voti v LEFT JOIN players p ON p.id = v.player_id "
                "  WHERE v.player_id IS NOT NULL AND p.id IS NULL) "
                "+ (SELECT count(*) FROM bonus_malus b "
                "  LEFT JOIN players p ON p.id = b.player_id "
                "  WHERE b.player_id IS NOT NULL AND p.id IS NULL)"
            )
        ).scalar()
    assert orphans == 0


@pytest.mark.parametrize("source", sorted(SOURCE_TO_TABLE))
def test_every_source_column_is_accounted_for(seeded: Engine, source: str) -> None:
    """Row counts cannot catch a dropped column: the count is identical either
    way. So every header column must map to a real column, a documented rename,
    or the explicit dropped list.
    """
    table = SOURCE_TO_TABLE[source]

    with (DATA_DIR / source).open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))

    with seeded.connect() as connection:
        columns = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            ).scalars()
        )

    unaccounted = []
    for column in header:
        if column in columns or RENAMED.get(column) in columns:
            continue
        if column in DROPPED and not (column == "nome" and table in _KEEPS_NOME):
            continue
        unaccounted.append(column)

    assert unaccounted == [], (
        f"{source}: {unaccounted} is neither a column of {table}, a documented "
        "rename, nor in the dropped list"
    )
