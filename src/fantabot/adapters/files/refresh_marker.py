"""The hourly refresh's marker on disk: which source last succeeded, and for which matchday.

One small JSON object at `config.lineup_refresh_path()`. It is the only reason the refresh
is cheap: without it every source runs every hour, and the voti scrape alone is eight GETs
against a live site.

**A marker that cannot be read is an empty marker, never an error.** A truncated or
hand-edited file means the refresh does its work again, which costs an hour's requests; a
refusal to start means it never does it at all, and the projection quietly goes stale. The
same argument the other way for writing: a write that fails is reported to the caller, so
a run that did the work and could not record it is visible rather than silently repeated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fantabot.domain.lineup.refresh import Marker, SourceRecord


class FileMarkerStore:
    """Reads and writes the refresh marker. The only file access on the refresh path."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        """Resolved at use, not at construction: `lineup_refresh_path` reads `Path.home()`,
        and a test that repoints the home directory after building this would otherwise
        keep writing to the operator's own."""
        if self._path is not None:
            return self._path
        from fantabot.config import lineup_refresh_path

        return lineup_refresh_path()

    def read(self) -> Marker:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Marker({})
        if not isinstance(raw, dict):
            return Marker({})
        records: dict[str, SourceRecord] = {}
        for source, entry in raw.items():
            record = _record(entry)
            if record is not None:
                records[str(source)] = record
        return Marker(records)

    def write(self, marker: Marker) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    source: {"at": r.at, "cmday": r.cmday, "detail": r.detail}
                    for source, r in sorted(marker.records.items())
                },
                indent=1,
                sort_keys=False,
            )
            + "\n",
            encoding="utf-8",
        )


def _record(entry: Any) -> SourceRecord | None:
    """One entry, or `None` when it is not one. A half-written record is no record."""
    if not isinstance(entry, dict):
        return None
    at, cmday = entry.get("at"), entry.get("cmday")
    if not isinstance(at, str) or not isinstance(cmday, int):
        return None
    detail = entry.get("detail")
    return SourceRecord(at=at, cmday=cmday, detail=detail if isinstance(detail, str) else "")
