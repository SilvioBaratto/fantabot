"""Is the database's schema the one this code expects? Two revision sets in, one verdict out.

A database left behind the code's migrations fails every write that touches a column the
code no longer has, and the job log says only "lega: failed - IntegrityError". `fantabot db
check` reports a declared-but-missing *table*, so a column-only migration is invisible to it;
only a comparison of the database's revision with the code's head can say the schema is
stale.

**This module is only the comparison.** Reading `alembic_version` is the persistence
adapter's job and reading the scripts' head is `adapters/persistence/alembic_scripts.py`'s;
what is left is a pure function of two small values, which is why every state — including
the ones a test machine could never produce, like a wheel install with no `alembic.ini` —
is pinned without a database or an alembic project.

**The states are kept apart because their remedies are.** *Behind* is `fantabot db dump`
and then `alembic upgrade head`. *Empty* — no application table at all — is `alembic upgrade
head` without the dump, because there is nothing to lose and a dump of nothing is noise.
*Unknown* — a revision these scripts have never heard of — must not be told to upgrade: the
database was migrated by a checkout that has migrations this one lacks, and upgrading from
here can only fail confusingly. *Unversioned* — tables but no `alembic_version` — must not
be told to upgrade either, because `upgrade head` would try to create tables that exist.
*Scripts unavailable* is not a mismatch, only an install that cannot compare. And
*unreadable* — the database could not be asked, or the migrations could not be loaded — is a
verdict and not an exception, because the hourly lineup job asks this before it submits and
a diagnostic must never be the reason a matchday goes unfielded.

**A migration is never run from here.** A migration can be destructive, so the operator
decides, with a dump taken first. The instruction says so in that order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class SchemaState(StrEnum):
    """Where the database's schema stands against the code's migrations."""

    AT_HEAD = "at-head"
    BEHIND = "behind"
    EMPTY = "empty"
    AHEAD_OR_UNKNOWN = "ahead-or-unknown"
    UNVERSIONED = "unversioned"
    SCRIPTS_UNAVAILABLE = "scripts-unavailable"
    UNREADABLE = "unreadable"


Severity = Literal["ok", "warn", "error"]


@dataclass(frozen=True)
class ScriptRevisions:
    """The code's side: which revisions the migration scripts define.

    Attributes:
        heads: The head revision(s). More than one means a branched history, and a
            database is at head only when it holds all of them.
        pending: Every revision the scripts define, mapped to how many migrations
            `alembic upgrade head` would apply from it, or `None` when the adapter could
            not count (a branch the walk cannot order). The keys are the set a database
            revision is checked against for "unknown".
    """

    heads: tuple[str, ...]
    pending: Mapping[str, int | None]


@dataclass(frozen=True)
class SchemaVerdict:
    """The comparison's answer, with enough to say it and to act on it.

    Attributes:
        state: Which case this is.
        database: The revision(s) the database holds; empty when there are none to name.
        heads: The code's head revision(s); empty when the scripts could not be read.
        behind: How many migrations are pending, or `None` when it cannot be stated — more
            than one database row, or an adapter that could not count.
        table_missing: For `UNVERSIONED` only, whether `alembic_version` does not exist
            (`True`) or exists and holds no row (`False`): different histories, reported
            differently.
        error: For `UNREADABLE` only, the exception's type name. Never its message, which
            carries the DSN.
    """

    state: SchemaState
    database: tuple[str, ...] = ()
    heads: tuple[str, ...] = ()
    behind: int | None = None
    table_missing: bool = False
    error: str | None = None

    @property
    def at_head(self) -> bool:
        """Whether nothing needs doing. Only `AT_HEAD` is; an unreadable schema is not."""
        return self.state is SchemaState.AT_HEAD

    @property
    def severity(self) -> Severity:
        """How loudly a surface should say it: `ok` is green, `warn` yellow, `error` red.

        `error` is for the states where the database and the code *disagree about what
        exists*: behind (writes already fail against the columns the code dropped), an unknown
        revision, and no revision at all. `warn` is for the ones that are expected on the way
        to a working database (empty) or merely unverifiable (scripts unavailable, unreadable).
        """
        if self.state is SchemaState.AT_HEAD:
            return "ok"
        if self.state in (
            SchemaState.BEHIND,
            SchemaState.AHEAD_OR_UNKNOWN,
            SchemaState.UNVERSIONED,
        ):
            return "error"
        return "warn"

    @property
    def summary(self) -> str:
        """One sentence, without the word "schema": each surface supplies its own label."""
        database = ", ".join(self.database)
        heads = ", ".join(self.heads)
        if self.state is SchemaState.AT_HEAD:
            return f"at head {heads}"
        if self.state is SchemaState.BEHIND:
            distance = (
                ""
                if self.behind is None
                else f" ({self.behind} revision{'' if self.behind == 1 else 's'} to apply)"
            )
            plural = "s" if len(self.heads) > 1 else ""
            return f"behind the code: database at {database}, code head{plural} {heads}{distance}"
        if self.state is SchemaState.EMPTY:
            return "empty: the database has no tables yet"
        if self.state is SchemaState.AHEAD_OR_UNKNOWN:
            return (
                f"database revision {database} is not in this checkout's alembic scripts "
                "(the database is ahead of this code, or another checkout migrated it)"
            )
        if self.state is SchemaState.UNVERSIONED:
            return (
                "unversioned: the database has no alembic_version table"
                if self.table_missing
                else "unversioned: alembic_version holds no revision"
            )
        if self.state is SchemaState.SCRIPTS_UNAVAILABLE:
            return f"database at {database}; no alembic scripts here to compare it against"
        return f"could not be checked ({self.error})"

    @property
    def _target(self) -> str:
        """What to hand `alembic upgrade`: `heads` for a branched history, where `head` is
        refused as ambiguous, and `head` otherwise."""
        return "heads" if len(self.heads) > 1 else "head"

    @property
    def instruction(self) -> str | None:
        """What the operator does about it, or `None` when there is nothing to do.

        Never an upgrade for a state that cannot be safely upgraded, and never one over
        existing tables without the dump first.
        """
        if self.state is SchemaState.BEHIND:
            return f"Take `fantabot db dump` first, then run `alembic upgrade {self._target}`."
        if self.state is SchemaState.EMPTY:
            return f"Create the schema with `alembic upgrade {self._target}`."
        if self.state is SchemaState.AHEAD_OR_UNKNOWN:
            return (
                "Run from the checkout that migrated this database, or bring this one up "
                "to date; do not migrate from here."
            )
        if self.state is SchemaState.UNVERSIONED:
            return (
                "Take `fantabot db dump` first; alembic cannot say what is missing, so "
                "compare against the models before stamping or migrating."
            )
        if self.state is SchemaState.SCRIPTS_UNAVAILABLE:
            return "From a source checkout, compare `alembic current` with `alembic heads`."
        return None


def judge(
    database: Sequence[str] | None,
    scripts: ScriptRevisions | None,
    *,
    empty: bool = False,
) -> SchemaVerdict:
    """Compare the database's revision(s) with the code's.

    Args:
        database: The rows of `alembic_version`. `None` means the table does not exist;
            an empty sequence means it exists and holds nothing.
        scripts: The code's side, or `None` when the scripts could not be found (an
            installed wheel has no `alembic.ini` beside it).
        empty: Whether the database holds none of the application's tables. Only read when
            there is no revision: a fresh database has none either way, and "unversioned"
            would send its operator to a dump and a comparison of nothing.

    Returns:
        The verdict. The database's own state is judged first: an unversioned database is
        unversioned whatever this install can see.
    """
    if not database:
        if empty:
            return SchemaVerdict(SchemaState.EMPTY, heads=scripts.heads if scripts else ())
        return SchemaVerdict(SchemaState.UNVERSIONED, table_missing=database is None)
    held = tuple(database)
    if scripts is None:
        return SchemaVerdict(SchemaState.SCRIPTS_UNAVAILABLE, database=held)
    if set(held) == set(scripts.heads):
        return SchemaVerdict(SchemaState.AT_HEAD, database=held, heads=scripts.heads)
    if any(revision not in scripts.pending for revision in held):
        return SchemaVerdict(
            SchemaState.AHEAD_OR_UNKNOWN, database=held, heads=scripts.heads
        )
    # The distance is a claim about one position on one line. Two rows is a branched
    # database, and a count that is 0 for a revision that is not every head is a count of
    # the wrong thing, so neither is reported as a number.
    behind = scripts.pending[held[0]] if len(held) == 1 else None
    return SchemaVerdict(
        SchemaState.BEHIND,
        database=held,
        heads=scripts.heads,
        behind=behind or None,
    )


def unreadable(error: str) -> SchemaVerdict:
    """The verdict for a comparison that could not be made: the database could not be asked,
    or the migration scripts could not be loaded.

    Args:
        error: The exception's type name. Not its message: a driver's message carries the
            connection string, and this lands in logs that get pasted.
    """
    return SchemaVerdict(SchemaState.UNREADABLE, error=error)
