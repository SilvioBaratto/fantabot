"""Health, introspection and truncation — what ``fantabot db check`` reports.

The table list is derived from ``Base.metadata`` rather than written out.
optimizer's equivalent keeps a hand-maintained ``APP_TABLES`` literal, which
drifts the moment a migration lands without someone remembering to edit it.
Deriving it means a table added in a later phase appears in ``db check`` and in
the truncate allowlist with no code change here.

Truncation is allowlisted because the table name reaches SQL as an identifier
and cannot be bound as a parameter. Any name that is not a real table in the
metadata is rejected before a statement is built.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import MetaData, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.base import Base
from fantabot.adapters.persistence.repositories._base import RepositoryBase

_PUBLIC_SCHEMA = "public"


class UnknownTableError(ValueError):
    """Raised for a table name that is not in the metadata."""


#: A phrase PostgreSQL puts in the connection error -> the name ``db check`` gives it.
#:
#: **Not SQLSTATE, and that was the first attempt.** A connection that never opened has no
#: ``PGresult``, so psycopg2 attaches no code to it: measured 2026-09-24 against the bundled
#: server, ``pgcode`` is ``None`` for a missing database, an unknown role *and* a refused
#: connection alike, and the three SQLSTATEs that name those cases (``3D000``, ``28000``,
#: ``28P01``) can never arrive. A map keyed on them answered ``unreachable`` for all three —
#: an affirmative wrong claim, where the ``(False, latency)`` it replaced at least claimed
#: nothing. The server is running and answering "no"; ``unreachable``'s documented fix is
#: ``fantabot-app db start``, which would send the operator to the one place that is fine.
#:
#: So this matches the message instead, which is the only signal the driver gives. It is
#: matched in lower case on the substring PostgreSQL has emitted for these conditions for
#: many major versions. ``LC_MESSAGES`` can translate them, and then nothing matches and the
#: answer is ``None`` — see ``classify_failure``.
_FAILURE_BY_PHRASE = (
    ("password authentication failed", "credentials"),
    ('role "', "credentials"),
    ('database "', "no-database"),
    ("connection refused", "unreachable"),
    ("could not connect", "unreachable"),
    ("no such file or directory", "unreachable"),
    ("server closed the connection", "unreachable"),
)


@dataclass(frozen=True)
class HealthReport:
    """``(ok, latency_ms)``, plus **which** failure — the thing ``(False, latency)`` lost.

    ``health`` answered every failure with ``False``, so on ``fantabot db check`` "the server
    is not running" and "the password is wrong" printed the same red word. That screen is the
    one an operator reads *precisely* when the CLI and the app disagree about which database
    they are on (``CLAUDE.md``, "One database, and it is the app's"), and the two have
    opposite fixes: start the server, or fix the DSN.

    ``failure`` is one of ``credentials``, ``no-database``, ``unreachable`` (the connection
    itself never came up) or ``query`` (the connection is fine and ``SELECT 1`` still failed),
    and is ``None`` when ``ok``.

    **``error`` is the exception's type name and never its message**, the same rule
    ``application/containment.py`` keeps and for the same reason: a driver's message carries
    the connection string, and this value is rendered on a screen that gets pasted into
    chats. The classification is what carries the meaning; the message would only carry the
    DSN, and ``application/config_report.safe_dsn`` is the one place allowed to render that.
    """

    ok: bool
    latency_ms: float
    failure: str | None = None
    error: str | None = None


class AdminRepository(RepositoryBase):
    """Introspection over whatever tables ``Base.metadata`` currently declares."""

    def __init__(self, session: Session, metadata: MetaData | None = None) -> None:
        super().__init__(session)
        self._metadata = metadata if metadata is not None else Base.metadata

    @property
    def table_names(self) -> list[str]:
        """Every table the application declares, in dependency order."""
        return [table.name for table in self._metadata.sorted_tables]

    def health(self) -> tuple[bool, float]:
        """``(ok, latency_ms)`` from a single ``SELECT 1``. See ``health_report`` for why."""
        report = self.health_report()
        return report.ok, report.latency_ms

    def health_report(self) -> HealthReport:
        """``SELECT 1``, and what went wrong if it did not answer.

        **Only ``SQLAlchemyError`` is absorbed.** It was a bare ``except Exception``, which
        made every failure in this method indistinguishable — including the ones that are not
        database failures at all. A ``TypeError`` from a session wired wrong is not an
        unhealthy database, and reporting it as one is how a bug becomes a week of looking at
        Postgres. Anything that is not a ``SQLAlchemyError`` propagates, the same rule
        ``application/containment.py`` keeps for ``AssertionError``.

        A database that is down, refusing the password, or missing is a ``SQLAlchemyError``
        every time, so ``fantabot db check`` still prints its screen rather than a traceback
        for every case it exists to report — it just now knows which one it is looking at.
        """
        start = time.perf_counter()
        try:
            self.session.execute(text("SELECT 1")).fetchone()
        except SQLAlchemyError as exc:
            return HealthReport(
                ok=False,
                latency_ms=_elapsed_ms(start),
                failure=classify_failure(exc),
                error=type(exc).__name__,
            )
        return HealthReport(ok=True, latency_ms=_elapsed_ms(start))

    def table_stats(self) -> list[dict[str, Any]]:
        """Row count and on-disk size per table, missing tables included."""
        return [self._stats_for(name) for name in self.table_names]

    def _stats_for(self, name: str) -> dict[str, Any]:
        if not self._exists(name):
            return {
                "name": name,
                "exists": False,
                "row_count": None,
                "size_bytes": None,
                "size_pretty": "—",
            }
        return {
            "name": name,
            "exists": True,
            "row_count": self._row_count(name),
            "size_bytes": self._size_bytes(name),
            "size_pretty": self._size_pretty(name),
        }

    def _exists(self, name: str) -> bool:
        result = self.session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = :name)"
            ),
            {"schema": _PUBLIC_SCHEMA, "name": name},
        )
        return bool(result.scalar())

    def _row_count(self, name: str) -> int | None:
        # Interpolated, not bound: an identifier cannot be a parameter. Safe
        # only because _require_known has vetted the name against the metadata.
        self._require_known(name)
        return self.session.execute(text(f'SELECT count(*) FROM "{name}"')).scalar()

    def _size_bytes(self, name: str) -> int | None:
        return self.session.execute(
            text("SELECT pg_total_relation_size(format('%I.%I', :schema, :name)::regclass)"),
            {"schema": _PUBLIC_SCHEMA, "name": name},
        ).scalar()

    def _size_pretty(self, name: str) -> str:
        value = self.session.execute(
            text(
                "SELECT pg_size_pretty(pg_total_relation_size("
                "format('%I.%I', :schema, :name)::regclass))"
            ),
            {"schema": _PUBLIC_SCHEMA, "name": name},
        ).scalar()
        return str(value or "—")

    def _require_known(self, name: str) -> None:
        if name not in self._metadata.tables:
            raise UnknownTableError(
                f"{name!r} is not a table this application declares. Known: "
                f"{', '.join(sorted(self._metadata.tables))}"
            )

    def truncate(self, name: str) -> None:
        """``TRUNCATE ... CASCADE`` one table. Rejects anything unknown first."""
        self._require_known(name)
        self.session.execute(text(f'TRUNCATE TABLE "{name}" CASCADE'))


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def classify_failure(exc: SQLAlchemyError) -> str | None:
    """Which failure this is, or ``None`` when the driver does not say.

    ``None`` is a real answer and the important one. A wrong name on this screen is worse
    than no name: it is the screen an operator reads *precisely* when the CLI and the app
    disagree about which database they are on (``CLAUDE.md``, "One database, and it is the
    app's"), and the cases have opposite fixes — start the server, or fix the DSN. So an
    unrecognised message reports the failure without guessing at its cause.

    **The message is read and never rendered.** It carries the connection string — measured,
    the socket path is in every one of these — and ``HealthReport.error`` is a type name for
    that reason. What crosses the boundary is the classification, not the text.
    """
    if not isinstance(exc, OperationalError):
        # The connection is up and ``SELECT 1`` still failed, which is not a connectivity
        # question at all.
        return "query"
    message = str(getattr(exc, "orig", exc)).lower()
    for phrase, named in _FAILURE_BY_PHRASE:
        if phrase in message:
            return named
    return None
