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

**Record v2** adds ids beside the names, the coordinates, the model, and a shadow plan. Every
new field has a default, so the 12-key v1 lines still parse. The **ids** are what grading
reads; names are for the eye, and two players can share one. Every value is coerced to a
JSON-native type at construction, because the projection deals in numpy scalars and
`json.dumps` refuses them. The **run** is the record, and the shadow is a comparison riding
on it: a shadow that still will not serialize is dropped with a note in `warnings`, never
the run with it.
"""

from __future__ import annotations

import json
import operator
from dataclasses import asdict, dataclass, replace
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
class LineupRejection:
    """One plan the walk did not keep: refused by the platform, or skipped by our own guard."""

    module: str
    #: `LUP0xx`, or `GUARD <slot> <role> <cell>` for a plan skipped before its POST.
    code: str
    #: The platform's own sentence. `""` for a guard skip, and when the platform sent none.
    message: str = ""
    #: The XI as it was laid out, in the platform's slot order: what was actually refused.
    starter_ids: tuple[int, ...] = ()
    starters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _native(
            self,
            module=str(self.module),
            code=str(self.code),
            message=str(self.message),
            starter_ids=_indexes(self.starter_ids),
            starters=_strs(self.starters),
        )


@dataclass(frozen=True, slots=True)
class LineupShadow:
    """The plan the other model would have sent, logged beside the one that went in."""

    model: str
    module: str
    #: In the platform's order, the same order `starts[]` and `bench[]` would be POSTed in.
    starter_ids: tuple[int, ...]
    bench_ids: tuple[int, ...]
    starters: tuple[str, ...]
    bench: tuple[str, ...]
    #: E[league points], the objective: `3·P(W) + P(D)`.
    e_pts: float
    #: P(win), P(draw), P(loss).
    p_wdl: tuple[float, float, float]
    #: E[fantapunti] and its spread, reported beside the objective.
    e_fp: float
    sd: float
    #: What the work-unit budget cut to finish in time (for example `M 64 -> 32`).
    cuts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _native(
            self,
            model=str(self.model),
            module=str(self.module),
            starter_ids=_indexes(self.starter_ids),
            bench_ids=_indexes(self.bench_ids),
            starters=_strs(self.starters),
            bench=_strs(self.bench),
            e_pts=float(self.e_pts),
            p_wdl=tuple(float(p) for p in self.p_wdl),
            e_fp=float(self.e_fp),
            sd=float(self.sd),
            cuts=_strs(self.cuts),
        )


@dataclass(frozen=True, slots=True)
class LineupRun:
    """One run, as the app will render it. Names for the eye, and ids beside them."""

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
    #: `module (code)` for each module the platform refused before one stuck — the display
    #: line, and all a v1 line has. `rejections` is the same walk with its evidence.
    rejected: tuple[str, ...] = ()
    # --- v2: every field below defaults to what a v1 line did not record ---
    starter_ids: tuple[int, ...] = ()
    bench_ids: tuple[int, ...] = ()
    competition: int | None = None
    tid: int | None = None
    #: The model that chose the lineup that was sent. `""` on a v1 line, which never said.
    model: str = ""
    #: Why the named model was not the one used, when it was not. `""` otherwise.
    fallback: str = ""
    warnings: tuple[str, ...] = ()
    rejections: tuple[LineupRejection, ...] = ()
    shadow: LineupShadow | None = None

    def __post_init__(self) -> None:
        _native(
            self,
            at=str(self.at),
            league=operator.index(self.league),
            scheduled=bool(self.scheduled),
            status=str(self.status),
            code=str(self.code),
            detail=str(self.detail),
            module=str(self.module),
            matchday=_index_or_none(self.matchday),
            serie_a_matchday=_index_or_none(self.serie_a_matchday),
            starters=_strs(self.starters),
            bench=_strs(self.bench),
            rejected=_strs(self.rejected),
            starter_ids=_indexes(self.starter_ids),
            bench_ids=_indexes(self.bench_ids),
            competition=_index_or_none(self.competition),
            tid=_index_or_none(self.tid),
            model=str(self.model),
            fallback=str(self.fallback),
            warnings=_strs(self.warnings),
            rejections=tuple(self.rejections),
        )


def append_run(path: Path, run: LineupRun) -> bool:
    """Append one run. **Never raises**; returns whether the line was written.

    By the time this runs the lineup has already gone in, or already failed. A record that
    raised would turn a submitted lineup into a crashed command, so a failure to write is
    reported to the caller instead — which says so on the terminal and in `launchd`'s log.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = _line(run)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except (OSError, TypeError, ValueError):
        return False
    return True


def _line(run: LineupRun) -> str:
    """The run as one JSON line, and **without its shadow** when the shadow will not serialize.

    `allow_nan=False` because a NaN is not JSON: Python would write it, and the app's
    response would then refuse the whole history. The note names the exception's type only.
    """
    try:
        return json.dumps(asdict(run), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        if run.shadow is None:
            raise
        note = f"shadow not written: {type(exc).__name__}"
        bare = replace(run, shadow=None, warnings=(*run.warnings, note))
        return json.dumps(asdict(bare), ensure_ascii=False, allow_nan=False)


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
        starter_ids=_items(raw.get("starter_ids")),
        bench_ids=_items(raw.get("bench_ids")),
        competition=_int_or_none(raw.get("competition")),
        tid=_int_or_none(raw.get("tid")),
        model=str(raw.get("model") or ""),
        fallback=str(raw.get("fallback") or ""),
        warnings=_texts(raw.get("warnings")),
        rejections=tuple(_rejection(item) for item in _items(raw.get("rejections"))),
        shadow=None if raw.get("shadow") is None else _shadow(raw["shadow"]),
    )


def _rejection(raw: Any) -> LineupRejection:
    if not isinstance(raw, dict):
        raise TypeError("a rejection is a JSON object")
    return LineupRejection(
        module=str(raw["module"]),
        code=str(raw["code"]),
        message=str(raw.get("message") or ""),
        starter_ids=_items(raw.get("starter_ids")),
        starters=_texts(raw.get("starters")),
    )


def _shadow(raw: Any) -> LineupShadow:
    if not isinstance(raw, dict):
        raise TypeError("a shadow is a JSON object")
    return LineupShadow(
        model=raw["model"],
        module=raw["module"],
        starter_ids=_items(raw["starter_ids"]),
        bench_ids=_items(raw["bench_ids"]),
        starters=_texts(raw["starters"]),
        bench=_texts(raw["bench"]),
        e_pts=raw["e_pts"],
        p_wdl=_items(raw["p_wdl"]),
        e_fp=raw["e_fp"],
        sd=raw["sd"],
        cuts=_texts(raw.get("cuts")),
    )


def _native(obj: object, **values: Any) -> None:
    """Set a frozen dataclass's fields to their coerced values, in `__post_init__`."""
    for name, value in values.items():
        object.__setattr__(obj, name, value)


def _index_or_none(value: Any) -> int | None:
    return None if value is None else operator.index(value)


def _indexes(value: Any) -> tuple[int, ...]:
    """Ids as plain ints. `operator.index`, not `int`, so a float id raises, never truncates."""
    return tuple(operator.index(item) for item in value)


def _strs(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in value)


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _items(value: Any) -> tuple[Any, ...]:
    return tuple(value) if isinstance(value, list | tuple) else ()


def _texts(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list | tuple) else ()


__all__ = [
    "FAILED",
    "SKIPPED",
    "STATUSES",
    "SUBMITTED",
    "UNCONFIRMED",
    "LineupRejection",
    "LineupRun",
    "LineupShadow",
    "append_run",
    "read_runs",
]
