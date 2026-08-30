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
from typing import Any

from sqlalchemy import MetaData, text
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.base import Base
from fantabot.adapters.persistence.repositories._base import RepositoryBase

_PUBLIC_SCHEMA = "public"


class UnknownTableError(ValueError):
    """Raised for a table name that is not in the metadata."""


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
        """``(ok, latency_ms)`` from a single ``SELECT 1``."""
        start = time.perf_counter()
        try:
            self.session.execute(text("SELECT 1")).fetchone()
        except Exception:
            return False, round((time.perf_counter() - start) * 1000, 2)
        return True, round((time.perf_counter() - start) * 1000, 2)

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
