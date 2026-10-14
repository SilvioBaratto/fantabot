"""The adapter reads a real `alembic_version`, on a real Postgres.

Everything else about the schema comparison is pinned against fakes, which is also why this
file exists: a fake session answers whatever it was told to, and so cannot say whether the
existence probe (`information_schema.tables`, `public`) and the select actually agree with
what alembic writes. Marked `db`, so it runs against `fantabot_test` and never the working
database.

**Every state is made inside the test's rolled-back transaction.** Postgres DDL is
transactional, so dropping or recreating `alembic_version` here is undone at teardown and the
test database is left exactly as it was found — whether or not it has been migrated.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, MetaData, String, Table, text
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.alembic_scripts import load_script_revisions
from fantabot.adapters.persistence.repositories.admin import AdminRepository
from fantabot.application.schema_check import check_schema
from fantabot.domain.shared.schema_revision import SchemaState

pytestmark = pytest.mark.db

CREATE = (
    "CREATE TABLE IF NOT EXISTS alembic_version ("
    "version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
)


def _hold(session: Session, *revisions: str) -> None:
    """Make `alembic_version` hold exactly `revisions`, inside the open transaction."""
    session.execute(text(CREATE))
    session.execute(text("DELETE FROM alembic_version"))
    for revision in revisions:
        session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev)"), {"rev": revision}
        )


def test_the_revision_alembic_wrote_is_the_revision_read(db_session: Session) -> None:
    """One row in, the same one out, through the real existence probe and the real select."""
    _hold(db_session, "b61ab22e8fac")

    assert AdminRepository(db_session).schema_revisions() == ("b61ab22e8fac",)


def test_a_branched_database_reads_every_row(db_session: Session) -> None:
    """Two stamped heads are two rows, and both come back."""
    _hold(db_session, "aaa111", "bbb222")

    assert AdminRepository(db_session).schema_revisions() == ("aaa111", "bbb222")


def test_an_empty_alembic_version_is_an_empty_tuple(db_session: Session) -> None:
    """Stamped to base: the table exists and holds nothing."""
    _hold(db_session)

    assert AdminRepository(db_session).schema_revisions() == ()


def test_no_alembic_version_table_is_none_and_leaves_the_session_usable(
    db_session: Session,
) -> None:
    """The absence is answered without a failed statement: a failed select would abort the
    transaction and the next query on the same session would raise."""
    db_session.execute(text("DROP TABLE IF EXISTS alembic_version"))

    assert AdminRepository(db_session).schema_revisions() is None
    assert db_session.execute(text("SELECT 1")).scalar() == 1


def test_the_verdict_for_a_real_database_at_and_behind_the_code_head(
    db_session: Session,
) -> None:
    """End to end on real rows and this checkout's real scripts."""
    scripts = load_script_revisions()
    assert scripts is not None
    head = scripts.heads[0]
    behind = next(rev for rev, pending in scripts.pending.items() if pending == 1)

    _hold(db_session, head)
    at_head = check_schema(AdminRepository(db_session).schema_revisions)
    _hold(db_session, behind)
    short = check_schema(AdminRepository(db_session).schema_revisions)

    assert at_head.state is SchemaState.AT_HEAD
    assert short.state is SchemaState.BEHIND
    assert short.behind == 1


def _declaring(*names: str) -> MetaData:
    """A metadata that declares one empty table per name, and creates none of them."""
    metadata = MetaData()
    for name in names:
        Table(name, metadata, Column("id", String, primary_key=True))
    return metadata


def test_a_database_with_none_of_the_declared_tables_is_empty(db_session: Session) -> None:
    """The existence probe against the real `information_schema`, for a name nobody made."""
    repo = AdminRepository(db_session, metadata=_declaring("fantabot_absent_a", "fantabot_absent_b"))

    assert repo.is_empty() is True


def test_one_declared_table_that_exists_makes_it_not_empty(db_session: Session) -> None:
    """`alembic_version` stands in for an application table: present is present."""
    _hold(db_session, "b61ab22e8fac")
    repo = AdminRepository(db_session, metadata=_declaring("fantabot_absent_a", "alembic_version"))

    assert repo.is_empty() is False


def test_the_waits_are_bounded_for_the_rest_of_the_transaction(db_session: Session) -> None:
    """`set_config(..., true)` against a real server: both settings read back in seconds, and
    they are the transaction's own, so the pooled connection is not left bounded."""
    AdminRepository(db_session).bound_waits(5)

    assert db_session.execute(text("SHOW statement_timeout")).scalar() == "5s"
    assert db_session.execute(text("SHOW lock_timeout")).scalar() == "5s"


def test_a_statement_that_outlasts_the_bound_is_cancelled(db_session: Session) -> None:
    """The point of the bound: a stuck read fails instead of stalling the launcher."""
    from sqlalchemy.exc import OperationalError

    AdminRepository(db_session).bound_waits(1)

    with pytest.raises(OperationalError):
        db_session.execute(text("SELECT pg_sleep(3)"))


def test_a_fresh_empty_database_reads_as_empty_not_unversioned(db_session: Session) -> None:
    """End to end on real rows: no `alembic_version` and none of the declared tables."""
    db_session.execute(text("DROP TABLE IF EXISTS alembic_version"))
    repo = AdminRepository(db_session, metadata=_declaring("fantabot_absent_a"))

    verdict = check_schema(repo.schema_revisions, read_empty=repo.is_empty)

    assert verdict.state is SchemaState.EMPTY
