"""Is the database at the code's migration head? The question three surfaces ask.

`fantabot db check`, `fantabot-app schedule status` and the top of every `fantabot-app
schedule run` all need the same answer, and one that is assembled three times is how three
screens come to disagree about whether the schema is current. The comparison is
`domain/shared/schema_revision`; the two reads are adapters; this is the wiring, and the
one rule that makes it safe to call from an unattended job.

**A failure to read the database is a verdict, not an exception.** The hourly lineup job
calls this before it submits, and a diagnostic that could stop a submit would cost more than
the drift it reports. On the database side only `SQLAlchemyError` is absorbed, for
`HealthReport`'s reason: a `TypeError` from a session wired wrong is not an unhealthy
database, and calling it one is how a bug becomes a week of looking at Postgres. The scripts
side absorbs any `Exception`, because loading them *executes* every migration module, and a
half-written one in a checkout somebody is editing raises whatever it likes. The job's own
launcher keeps the broadest guard — it must go on whatever happens — and names the exception
type when it fires.

**The check is bounded in time.** The launcher calls it before it spawns the submit, which
has walls of its own; a hung postmaster or a lock on `alembic_version` must not be able to
stall the one thing that matters in that hour. The bound is set in the session, before the
first read.

**Nothing here upgrades anything.** A migration can be destructive, so the operator decides,
with a dump taken first; this module answers "where is it" and stops.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.exc import SQLAlchemyError

from fantabot.adapters.persistence.alembic_scripts import load_script_revisions
from fantabot.domain.shared.schema_revision import SchemaVerdict, ScriptRevisions, judge, unreadable

#: How long either read may wait on the server or on a lock. Generous for two catalogue
#: lookups and a one-row select, and short against a launcher that runs every hour.
WAIT_SECONDS = 5


def check_schema(
    read_revisions: Callable[[], tuple[str, ...] | None],
    *,
    read_scripts: Callable[[], ScriptRevisions | None] | None = None,
    read_empty: Callable[[], bool] | None = None,
) -> SchemaVerdict:
    """Compare the database's revision with the code's head.

    Args:
        read_revisions: Returns the rows of `alembic_version`, or `None` when the table
            does not exist. May raise `SQLAlchemyError`, which becomes an `UNREADABLE`
            verdict.
        read_scripts: Returns the code's revisions, or `None` when the scripts are not
            there. Looked up when called, so a test can substitute the adapter; defaults to
            this checkout's own migrations. Anything it raises becomes an `UNREADABLE`
            verdict.
        read_empty: Returns whether the database holds none of the application's tables.
            Asked only when there is no revision to compare, and not at all when omitted
            (read as "not empty"). May raise `SQLAlchemyError`, like `read_revisions`.

    Returns:
        The verdict. The scripts are not read when the database could not be: loading every
        migration module to describe a connection failure is work for nothing.
    """
    try:
        held = read_revisions()
        empty = bool(read_empty()) if read_empty is not None and not held else False
    except SQLAlchemyError as exc:
        return unreadable(type(exc).__name__)
    try:
        scripts = (read_scripts or load_script_revisions)()
    except Exception as exc:
        # Broad on purpose: importing a migration module runs whatever it contains.
        return unreadable(type(exc).__name__)
    return judge(held, scripts, empty=empty)


def check_database_schema() -> SchemaVerdict:
    """`check_schema` against the configured database, through a session of its own.

    For callers that hold no session — the launcher's `schedule status` and `schedule run`.
    `db check` already has one open and passes its repository's read to `check_schema`
    instead. A server that is not running is the ordinary reason to land on `UNREADABLE`.
    """
    import fantabot.adapters.persistence.models  # noqa: F401  -- registers every table on Base.metadata
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.admin import AdminRepository

    try:
        with database_manager.get_session() as session:
            repo = AdminRepository(session)
            # The first statement on the connection, so a server that is down fails here and
            # not inside `check_schema`: this guard is what turns that into a verdict.
            repo.bound_waits(WAIT_SECONDS)
            return check_schema(repo.schema_revisions, read_empty=repo.is_empty)
    except SQLAlchemyError as exc:
        return unreadable(type(exc).__name__)
