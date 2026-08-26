"""CSV importers: the one-time seed that turns the flat files into tables.

Every importer is registered in ``REGISTRY``, and **the registry's order is the
load order**. It encodes dimensions-before-facts permanently: ``players`` and
``teams`` have no outbound foreign keys and must exist before anything that
points at them. Appending an importer in the wrong place is a foreign-key
violation on the next full run, not a style problem.

Importers are idempotent. A killed run is restarted, not repaired, so every one
of them upserts rather than inserts.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from fantabot.db.importers._csv import italian_decimal, plain_decimal, split_codes


@dataclass(frozen=True)
class ImportResult:
    """What one importer did. ``inserted + unchanged`` is the row count seen."""

    table: str
    inserted: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.unchanged


@dataclass(frozen=True)
class Importer:
    """One table's seed: which files it reads, and how to load them."""

    name: str
    sources: tuple[str, ...]
    load: Callable[[Session, Path], ImportResult]
    description: str = ""
    expected_rows: int | None = None
    depends_on: tuple[str, ...] = field(default_factory=tuple)

    def missing_sources(self, data_dir: Path) -> list[str]:
        """Source files that are not on disk. Empty means ready to run."""
        return [name for name in self.sources if not (data_dir / name).exists()]


# Load order is dependency order. Do not sort this.
REGISTRY: tuple[Importer, ...] = ()


def names() -> list[str]:
    """Every registered table name, in load order."""
    return [importer.name for importer in REGISTRY]


def get(name: str) -> Importer:
    """Look up one importer, or raise with the full list of valid names."""
    for importer in REGISTRY:
        if importer.name == name:
            return importer
    valid = ", ".join(names()) or "(none registered yet)"
    raise KeyError(f"{name!r} is not a known table. Valid: {valid}")


def resolve(*, every: bool, table: str | None) -> Sequence[Importer]:
    """The importers a command should run, in load order."""
    if every:
        return REGISTRY
    if table is None:
        return ()
    return (get(table),)


__all__ = [
    "REGISTRY",
    "ImportResult",
    "Importer",
    "get",
    "italian_decimal",
    "names",
    "plain_decimal",
    "resolve",
    "split_codes",
]
