"""What must be true of the database, asserted without a second copy to compare against.

This replaces the cross-source half of ``test_full_seed.py``. That suite proved
the tables matched the ten CSVs; once the CSVs are gone there is no independent
source, and "the database is correct" would become unfalsifiable unless the
properties are restated as things the data must satisfy *on its own terms*.

**Nothing here skips.** ``test_full_seed.py:87-90`` and ``test_db.py:628-630``
both call ``pytest.skip`` when their source files are absent, which means
deleting the CSVs would leave ``pytest -m db`` printing green while 21
cross-source checks quietly stopped running. A green suite that stopped checking
is worse than a red one. Every assertion below fails against an empty database,
and that is verified rather than assumed — ``pytest -m db`` against a freshly
migrated scratch database gives 17 failed, 0 passed.

**Why the constraints carry a non-emptiness precondition.** Half of these are
floors (``count >= n``) and half are constraints (``count of violations == 0``).
A constraint is *vacuously true* on an empty table: ``DELETE FROM quotazioni``
satisfies "every club code is three upper-case letters" perfectly. Measured —
without the preconditions below, four of these seventeen passed against an empty
database. A check that deleting the data would satisfy is not checking anything,
so each constraint first asserts it had rows to inspect.

**Idempotence moved.** ``test_full_seed.py``'s fixture ran the whole importer
registry twice and asserted the counts did not move — that was the proof the
seed was re-runnable. The scrapers carry that property now, in their
``ON CONFLICT`` clauses: ``upsert_two_passes`` for the match grain
(``db/upserts.py``), and the ``DO UPDATE``/``DO NOTHING`` clauses throughout
``scripts/_db.py``. A killed scrape is restarted, not repaired. Nothing here
re-runs a load to prove it, because the guard is now in the write path itself.

The row floors are **floors, not equalities**. The scrapers read a live site and
the site moves — a scrape on 2026-08-26 added four players to the 2026/27
listone. Pinning equality here would pin the weather. What must never happen is
a row going missing.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import Engine, create_engine, text

pytestmark = pytest.mark.db

# Measured from the ten CSVs before they were retired. Floors: at least this
# many rows must be present, forever.
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


@pytest.fixture(scope="module")
def engine() -> Generator[Engine, None, None]:
    """A plain engine on the configured database. No skipping, no seeding."""
    from fantabot.config import settings

    made = create_engine(settings.fantabot_database_url, pool_pre_ping=True)
    yield made
    made.dispose()


def _scalar(engine: Engine, sql: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(sql)).scalar() or 0)


# --- the floors -----------------------------------------------------------


@pytest.mark.parametrize(("table", "minimum"), sorted(MINIMUM_ROWS.items()))
def test_every_table_holds_at_least_the_rows_the_seed_had(
    engine: Engine, table: str, minimum: int
) -> None:
    assert _scalar(engine, f'SELECT count(*) FROM "{table}"') >= minimum


# --- the properties the CSVs used to witness ------------------------------


def test_the_coach_rows_are_present_in_both_match_tables(engine: Engine) -> None:
    """A NOT NULL foreign key would reject 3,039 rows per table and the totals
    above would still look plausible. This is the check that notices."""
    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM voti WHERE player_id IS NULL), "
                "(SELECT count(*) FROM bonus_malus WHERE player_id IS NULL)"
            )
        ).one()
    assert counts[0] >= 3039
    assert counts[1] >= 3039


def test_the_no_data_marker_never_became_a_zero(engine: Engine) -> None:
    """An absent average must be NULL. Zero is a real measurement and would be
    indistinguishable from one after a bad parse."""
    with engine.connect() as connection:
        zeros, nulls = connection.execute(
            text(
                "SELECT count(*) FILTER (WHERE media_voto = 0), "
                "count(*) FILTER (WHERE media_voto IS NULL) FROM statistiche"
            )
        ).one()
    assert zeros == 0
    assert nulls >= 2846


def test_nothing_is_orphaned_anywhere(engine: Engine) -> None:
    """Every foreign key across the schema, in one query."""
    inspected = _scalar(
        engine,
        "SELECT (SELECT count(*) FROM quotazioni) + (SELECT count(*) FROM statistiche) "
        "+ (SELECT count(*) FROM voti) + (SELECT count(*) FROM bonus_malus)",
    )
    assert inspected > 0, "no rows to orphan; the check would pass vacuously"

    orphans = _scalar(
        engine,
        "SELECT (SELECT count(*) FROM quotazioni q "
        "  LEFT JOIN players p ON p.id = q.player_id WHERE p.id IS NULL) "
        "+ (SELECT count(*) FROM statistiche s "
        "  LEFT JOIN players p ON p.id = s.player_id WHERE p.id IS NULL) "
        "+ (SELECT count(*) FROM voti v LEFT JOIN players p ON p.id = v.player_id "
        "  WHERE v.player_id IS NOT NULL AND p.id IS NULL) "
        "+ (SELECT count(*) FROM bonus_malus b "
        "  LEFT JOIN players p ON p.id = b.player_id "
        "  WHERE b.player_id IS NOT NULL AND p.id IS NULL)",
    )
    assert orphans == 0


# --- promoted out of the dying reader tests -------------------------------


def test_every_club_code_is_three_upper_case_letters(engine: Engine) -> None:
    """The composite foreign key to ``teams.codice`` misses an un-upper-cased
    code — ``mil`` and ``MIL`` are different strings and only one has a row."""
    with engine.connect() as connection:
        bad, inspected = connection.execute(
            text("SELECT count(*) FILTER (WHERE squadra !~ '^[A-Z]{3}$'), count(*) FROM quotazioni")
        ).one()
    assert inspected > 0, "no codes to check; the check would pass vacuously"
    assert bad == 0


def test_role_labels_keep_their_casing_while_codes_normalise(engine: Engine) -> None:
    """``news/prompt.py`` puts the label in front of a model, and ``ATTACCANTE``
    is not what a human wrote. The code is normalised; the label is not."""
    with engine.connect() as connection:
        shouty_labels, lower_codes, inspected = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM voti WHERE ruolo <> '' AND ruolo = upper(ruolo)), "
                "(SELECT count(*) FROM voti WHERE ruolo_codice <> upper(ruolo_codice)), "
                "(SELECT count(*) FROM voti WHERE ruolo <> '')"
            )
        ).one()
    assert inspected > 0, "no labels to check; the check would pass vacuously"
    assert shouty_labels == 0
    assert lower_codes == 0


def test_a_zero_forecast_is_kept_apart_from_an_absent_one(engine: Engine) -> None:
    """Blank means no forecast was made; 0.0 means one was made and came out
    flat. Collapsing them loses the distinction the flags depend on."""
    with engine.connect() as connection:
        zeros, nulls = connection.execute(
            text(
                "SELECT count(*) FILTER (WHERE predicted_pct_delta = 0), "
                "count(*) FILTER (WHERE predicted_pct_delta IS NULL) FROM target_price"
            )
        ).one()
    assert zeros >= 1
    assert nulls > 0


# --- facts that lived only in the deleted docstrings ----------------------


def test_players_is_the_union_of_the_listone_and_the_match_data(engine: Engine) -> None:
    """``players`` is filled from two places — the listone insert and the
    match-grain insert — so it legitimately holds ids no ``quotazioni`` row
    references. That union is why the count exceeds the listone's."""
    only_in_matches = _scalar(
        engine,
        "SELECT count(*) FROM players p "
        "WHERE NOT EXISTS (SELECT 1 FROM quotazioni q WHERE q.player_id = p.id)",
    )
    assert only_in_matches >= 60


def test_the_two_listoni_stay_in_step(engine: Engine) -> None:
    """Classic and Mantra are the same players priced two ways. A count that
    diverges means one leg of a scrape failed and the other did not."""
    with engine.connect() as connection:
        q_classic, q_mantra, b_classic, b_mantra = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM quotazioni WHERE listone='classic'), "
                "(SELECT count(*) FROM quotazioni WHERE listone='mantra'), "
                "(SELECT count(*) FROM qi_bias WHERE listone='classic'), "
                "(SELECT count(*) FROM qi_bias WHERE listone='mantra')"
            )
        ).one()
    assert q_classic > 0 and b_classic > 0, "both listoni empty; equality is vacuous"
    assert q_classic == q_mantra
    assert b_classic == b_mantra


def test_the_club_vocabularies_are_a_bijection(engine: Engine) -> None:
    """Every code resolves to exactly one name and vice versa. A collision here
    is what ``club_names.build_mapping`` refuses to write through."""
    with engine.connect() as connection:
        codes, names = connection.execute(
            text(
                "SELECT (SELECT count(DISTINCT codice) FROM teams), "
                "(SELECT count(DISTINCT nome_completo) FROM teams)"
            )
        ).one()
    assert codes == names
    assert codes >= 27
