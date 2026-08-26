"""What must be true of the database, asserted without a second copy to compare against.

This replaces the cross-source half of ``test_full_seed.py``. That suite proved
the tables matched the ten CSVs; once the CSVs are gone there is no independent
source, and "the database is correct" would become unfalsifiable unless the
properties are restated as things the data must satisfy *on its own terms*.

**Nothing here skips.** ``test_full_seed.py:87-90`` and ``test_db.py:628-630``
both call ``pytest.skip`` when their source files are absent, which means
deleting the CSVs would leave ``pytest -m db`` printing green while 21
cross-source checks quietly stopped running. A green suite that stopped checking
is worse than a red one. Every *data* invariant below fails against an empty
database, and that is verified rather than assumed: against a freshly migrated
scratch database this module gives **17 failed, 2 passed**. The two that pass
are the schema pins at the end, which read ``information_schema`` rather than
rows — a column exists or it does not, and no number of rows changes the answer.

**Why the constraints carry a non-emptiness precondition.** Half of these are
floors (``count >= n``) and half are constraints (``count of violations == 0``).
A constraint is *vacuously true* on an empty table: ``DELETE FROM quotazioni``
satisfies "every club code is three upper-case letters" perfectly. Measured —
without the preconditions below, four of these seventeen passed against an empty
database. A check that deleting the data would satisfy is not checking anything,
so each constraint first asserts it had rows to inspect.

**The schema pins replace the column-accounting check.**
``test_full_seed.py:171-175`` compared each CSV header against its table, because
*a dropped column changes no count* — every floor above stays green while a field
silently stops being written. That comparison dies with the files, but the drift
risk only moves: it is now ``scripts/_db.py``'s hand-written ``INSERT INTO``
column lists and ``updatable`` tuples drifting from the schema. Neither
``alembic check`` nor ``tests/test_migrations.py`` can see it — both compare the
*models* to the *migrations*, and these are SQL strings in a script that neither
one reads.

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

import ast
import re
from collections.abc import Generator
from pathlib import Path

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


# --- the schema pins: what the scrapers write must exist -------------------
#
# These differ from everything above: they read the *schema*, not the data, so
# unlike the data invariants they pass against an empty-but-migrated database.
# That is correct — a column either exists or it does not, and no number of rows
# changes the answer.


def _db_script_source() -> str:
    """``scripts/_db.py`` as text. Read, never imported.

    Importing it would execute the module and pull in its Session machinery; the
    property under test is what the file *says*, which is a syntax-level fact.
    ``scripts/`` is not a package and has no ``__init__.py``, so there is nothing
    importable here anyway.
    """
    return (Path(__file__).resolve().parents[2] / "scripts" / "_db.py").read_text()


# The class handed to ``upsert_two_passes`` as its second positional argument.
# Those calls carry no ``INSERT INTO`` string at all — the statement is built
# inside ``db/upserts.py`` from the model — so an AST walk keyed on table names
# cannot reach their ``updatable`` tuples and would silently cover nothing.
# Asserted exhaustive below: a third call site with an unmapped class fails.
MODEL_TO_TABLE: dict[str, str] = {"Voto": "voti", "BonusMalus": "bonus_malus"}

# Deliberate divergences, each with the reason beside it. This is the escape
# hatch that ``test_full_seed.py:49-67`` spelled RENAMED/DROPPED. A column that
# is genuinely written under another name belongs here as a documented line —
# never as a loosened assertion.
#
#   ("table", "column-named-in-_db.py"): "why it is not a column of that table"
DOCUMENTED_DIVERGENCES: dict[tuple[str, str], str] = {}


def _insert_column_lists(source: str) -> list[tuple[str, list[str], int]]:
    """Every ``INSERT INTO <table> (<columns>)`` in the file, as (table, columns, line).

    Adjacent string literals are folded by the parser into a single
    ``ast.Constant``, so the multi-line SQL arrives whole and one regex over the
    node's value sees the entire column list.
    """
    found: list[tuple[str, list[str], int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        for match in re.finditer(r"INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)", node.value, re.I):
            columns = [c.strip() for c in match.group(2).split(",") if c.strip()]
            found.append((match.group(1), columns, node.lineno))
    return found


def _updatable_tuples(source: str) -> list[tuple[str, list[str], int]]:
    """Every ``upsert_two_passes(..., Model, ..., updatable=(...))``, keyed by model."""
    found: list[tuple[str, list[str], int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "upsert_two_passes"):
            continue
        model = node.args[1]
        assert isinstance(model, ast.Name), "second argument is expected to be a model class"
        keyword = next(k for k in node.keywords if k.arg == "updatable")
        assert isinstance(keyword.value, ast.Tuple)
        columns = [e.value for e in keyword.value.elts if isinstance(e, ast.Constant)]
        found.append((model.id, columns, node.lineno))
    return found


def _columns_of(engine: Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        ).scalars()
        return set(rows)


def test_every_insert_names_only_real_columns(engine: Engine) -> None:
    """A dropped or renamed column changes no row count, so nothing above notices.

    ``alembic check`` cannot see this and neither can ``tests/test_migrations.py``:
    both compare the models to the migrations, and these column lists are
    hand-written SQL strings in a script that neither one reads.
    """
    inserts = _insert_column_lists(_db_script_source())
    assert len(inserts) >= 9, f"expected the known INSERT statements, found {len(inserts)}"

    problems: list[str] = []
    for table, columns, line in inserts:
        real = _columns_of(engine, table)
        assert real, f"scripts/_db.py:{line} writes to {table!r}, which is not a table"
        for column in columns:
            if column not in real and (table, column) not in DOCUMENTED_DIVERGENCES:
                problems.append(f"scripts/_db.py:{line}: {table}.{column} is not a column")
    assert not problems, "\n".join(problems)


def test_every_updatable_tuple_names_only_real_columns(engine: Engine) -> None:
    """The ``updatable`` tuples drift the same way, and are reached differently.

    They are attached to a model class rather than to an ``INSERT INTO`` string,
    so they need the mapping above; without it this test would walk zero nodes
    and pass for the wrong reason.
    """
    tuples = _updatable_tuples(_db_script_source())
    assert len(tuples) == 2, f"expected two upsert_two_passes call sites, found {len(tuples)}"

    unmapped = {model for model, _, _ in tuples} - set(MODEL_TO_TABLE)
    assert not unmapped, f"MODEL_TO_TABLE is not exhaustive: add {unmapped}"

    problems: list[str] = []
    for model, columns, line in tuples:
        table = MODEL_TO_TABLE[model]
        real = _columns_of(engine, table)
        assert real, f"{model} maps to {table!r}, which is not a table"
        assert columns, f"scripts/_db.py:{line}: {model}'s updatable tuple is empty"
        for column in columns:
            if column not in real and (table, column) not in DOCUMENTED_DIVERGENCES:
                problems.append(f"scripts/_db.py:{line}: {table}.{column} is not a column")
    assert not problems, "\n".join(problems)
