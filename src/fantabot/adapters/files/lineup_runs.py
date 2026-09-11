"""The scheduled lineup's run record: one JSONL line per `lineup submit --scheduled`.

The lineup is submitted unattended by a `launchd` job, and the app shows the history of those
runs, read-only — it is where the operator looks to learn whether the bot fielded a lineup,
and why not when it did not. The row schema, the writer and the reader live here together,
for `room_journal.py`'s reason: a schema stated in several places drifts, and a reader that
knows fewer fields than the writer renders the rows that matter most as nulls.

**It must never be able to wait on a database.** The runs most worth recording are the ones
that fail *because* nothing else works — the bundled Postgres not up after a reboot, a token
that no longer opens — so this module joins `CAPTURE` in `tests/application/test_aste_outage.py`
and is structurally barred from reaching persistence. Stdlib only.

**Opened per run, not held.** One line per invocation, several times a day; the room journal's
keep-it-open argument (a syscall every two seconds for three hours) does not apply.

Which rows are red is decided in `application/lineup_submit.run_record`, once. This module
only names the four states and carries them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

#: Reached the platform and was read back.
SUBMITTED = "submitted"
#: Reached the platform — the POST returned 200 — and could not be read back to prove it.
UNCONFIRMED = "unconfirmed"
#: A run doing its job by not acting: the matchday started, the start belongs to another
#: matchday, or the platform has not opened the next one yet.
SKIPPED = "skipped"
#: A run that should have submitted and did not.
FAILED = "failed"

STATUSES = (SUBMITTED, UNCONFIRMED, SKIPPED, FAILED)


@dataclass(frozen=True, slots=True)
class LineupRun:
    """One run, as the app will render it. Names rather than ids: a history is read by eye."""

    #: ISO 8601 with its offset — stamped by `interface/lineup.py::_now`, the lineup
    #: surface's one clock read.
    at: str
    league: int
    scheduled: bool
    status: str
    #: The refusal code or the exception class; empty on a clean submit.
    code: str = ""
    #: The sentence behind `code` — which lock is shut, which matchday started when.
    detail: str = ""
    module: str = ""
    #: The league's matchday and Serie A's, which differ: on 2026-09-10 league matchday 2 was
    #: Serie A matchday 4.
    matchday: int | None = None
    serie_a_matchday: int | None = None
    starters: tuple[str, ...] = ()
    bench: tuple[str, ...] = ()
    #: `module (code)` for each module the platform refused before one stuck.
    rejected: tuple[str, ...] = ()


def append_run(path: Path, run: LineupRun) -> bool:
    """Append one run. **Never raises**; returns whether the line was written.

    By the time this runs the lineup has already gone in, or already failed. A record that
    raised would turn a submitted lineup into a crashed command, so a failure to write is
    reported to the caller instead — which says so on the terminal and in `launchd`'s log.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(run), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except (OSError, TypeError, ValueError):
        return False
    return True


def read_runs(path: Path) -> tuple[tuple[LineupRun, ...], int]:
    """Every run, **newest first**, and the count of lines that did not parse.

    A torn line — `launchd` killing a run mid-write — is skipped and counted, not fatal: a
    history that vanished over one bad line would hide exactly the evening it exists to
    explain. A missing file is no runs yet, not an error.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return (), 0

    runs: list[LineupRun] = []
    skipped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            runs.append(_run(json.loads(line)))
        except (ValueError, TypeError, KeyError):
            skipped += 1
    return tuple(reversed(runs)), skipped


def _run(raw: Any) -> LineupRun:
    if not isinstance(raw, dict):
        raise TypeError("a run is a JSON object")
    return LineupRun(
        at=str(raw["at"]),
        league=int(raw["league"]),
        scheduled=bool(raw["scheduled"]),
        status=str(raw["status"]),
        code=str(raw.get("code") or ""),
        detail=str(raw.get("detail") or ""),
        module=str(raw.get("module") or ""),
        matchday=_int_or_none(raw.get("matchday")),
        serie_a_matchday=_int_or_none(raw.get("serie_a_matchday")),
        starters=_texts(raw.get("starters")),
        bench=_texts(raw.get("bench")),
        rejected=_texts(raw.get("rejected")),
    )


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _texts(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list | tuple) else ()


__all__ = [
    "FAILED",
    "SKIPPED",
    "STATUSES",
    "SUBMITTED",
    "UNCONFIRMED",
    "LineupRun",
    "append_run",
    "read_runs",
]
