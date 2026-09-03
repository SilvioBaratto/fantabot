"""Database health — the cockpit's System panel.

Reports whether the local Postgres answers and the per-table row counts. This endpoint
must never 500: it is the one that reports a down database, so it degrades open (returns
``ok=false`` with a reason) instead of raising. ``AdminRepository.health`` already
swallows connection errors; ``table_stats`` does not, so it is guarded separately.
"""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class TableStat(BaseModel):
    name: str
    exists: bool
    row_count: int | None = None
    size_pretty: str = "—"


class DbHealth(BaseModel):
    ok: bool
    latency_ms: float
    tables: list[TableStat]
    error: str | None = None


class _Admin(Protocol):
    def health(self) -> tuple[bool, float]: ...
    def table_stats(self) -> list[dict[str, Any]]: ...


def read_db_health(admin: _Admin) -> DbHealth:
    """Assemble a DbHealth from an AdminRepository-like object (pure; unit-testable)."""
    ok, latency = admin.health()
    tables: list[TableStat] = []
    if ok:
        try:
            tables = [
                TableStat(
                    name=row["name"],
                    exists=row["exists"],
                    row_count=row["row_count"],
                    size_pretty=row["size_pretty"],
                )
                for row in admin.table_stats()
            ]
        except Exception:  # noqa: BLE001 — a stats hiccup must not fail the health probe
            tables = []
    return DbHealth(ok=ok, latency_ms=latency, tables=tables)


@router.get("/db/health", response_model=DbHealth, tags=["system"])
def db_health() -> DbHealth:
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.admin import AdminRepository

    try:
        with database_manager.get_session() as session:
            return read_db_health(AdminRepository(session))
    except Exception as exc:  # noqa: BLE001 — report a down DB, don't crash
        return DbHealth(ok=False, latency_ms=0.0, tables=[], error=type(exc).__name__)
