"""Asta plan — the optimal roster for a lega, on its real (snapshotted) roster rules.

Mirrors `interface/asta.py`'s optimize command: read_plan_inputs -> optimize_roster. The
one difference is the point of S9: for Mantra it injects a RosterRules built from the
lega's latest LeagueSnapshot (size + min roles) instead of the RosterRules(size=30)
default. Read-only; degrades open (found=false on no data / DB error).

It also serves the **room journal** — `data/room_journal.jsonl`, which the CLI writes
and, until this endpoint, nothing read. Same page, per §7 of the archived phase spec
(`tasks/archive/fantalab-in-the-app-spec.md`): the room check and the journal are sections
of the Asta page rather than a tenth nav entry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class PlanPlayer(BaseModel):
    player_id: str
    nome: str
    price: float


class AstaPlan(BaseModel):
    found: bool
    listone: str = ""
    roster_size: int = 0
    total_cost: float = 0.0
    objective: float = 0.0
    budget: float = 0.0
    players: list[PlanPlayer] = []


def build_roster_rules(snapshot: Any) -> Any:
    """A Mantra RosterRules from the lega's snapshot (size + [gk_min, movement_min]).

    Falls back to the default RosterRules() when the snapshot lacks the fields — better
    the default than a crash, but the point is to plan on 25/32 not a hardcoded 30.
    """
    from fantabot.domain.asta.state import RosterRules

    if (
        snapshot is None
        or snapshot.roster_size is None
        or not snapshot.min_roles
        or len(snapshot.min_roles) < 2
    ):
        return RosterRules()
    return RosterRules(
        size=int(snapshot.roster_size),
        min_goalkeepers=int(snapshot.min_roles[0]),
        min_movement=int(snapshot.min_roles[1]),
    )


@router.get("/asta/plan", response_model=AstaPlan, tags=["asta"])
def asta_plan(league_id: int, season: str = "2026/27") -> AstaPlan:
    from fantabot.adapters.persistence import database_manager
    from fantabot.application.asta_planner import read_plan_inputs
    from fantabot.domain.asta.optimizer import optimize_roster
    from fantabot.domain.asta.state import AstaState
    from fantabot.domain.classic.state import ClassicRosterRules

    from fantabot_app.api.reads import league as reads

    try:
        with database_manager.get_session() as session:
            snapshot = reads.latest_settings(session, league_id)
            fmt = "classic" if (snapshot is not None and snapshot.role_groups == 1) else "mantra"
            budget = float(snapshot.budget) if snapshot and snapshot.budget else 500.0

            world = read_plan_inputs(
                session,
                season=season,
                sentiment=None,
                as_of=None,
                tilt_k=1.0,
                listone=fmt,
                num_credits=int(budget),
            )
            if not world.pool:
                return AstaPlan(found=False)

            rules = ClassicRosterRules() if fmt == "classic" else build_roster_rules(snapshot)
            result = optimize_roster(
                AstaState(total_budget=budget),
                world.pool,
                value=world.value,
                prices=world.prices,
                teams=world.teams,
                legality=world.legality,
                rules=rules,
                lam=0.0,
                n_fallbacks=0,
            )

        players = [
            PlanPlayer(
                player_id=pid,
                nome=world.names.get(pid, pid),
                price=float(world.prices.get(pid, 0.0)),
            )
            for pid in result.optimal.player_ids
        ]
        return AstaPlan(
            found=True,
            listone=fmt,
            roster_size=int(getattr(rules, "size", len(players))),
            total_cost=float(result.optimal.total_cost),
            objective=float(result.optimal.objective),
            budget=budget,
            players=players,
        )
    except Exception:  # noqa: BLE001 — degrade open: no data / infeasible / DB error
        return AstaPlan(found=False)


# -- the room journal ---------------------------------------------------------------------

#: What `interface/asta.py` writes, under `settings.fantabot_data_dir`. Both `asta room`
#: and `asta bid` append to the same file; neither marks a run boundary, which is why the
#: viewer pages a file and not an evening (the archived phase spec §9 leaves that open on
#: purpose — the marker would change the artefact the 2026-09-01 audit was done against).
JOURNAL_FILE = "room_journal.jsonl"
#: One page. The recorded evening is 5,192 rows and 1.6 MB of JSON; the whole of it in one
#: response is a viewer that renders once and then stalls the tab it opened in.
DEFAULT_JOURNAL_LIMIT = 100
MAX_JOURNAL_LIMIT = 500


class JournalRow(BaseModel):
    """One cycle: what was on the block, what we thought it was worth, and what we did.

    Every field is optional because the file spans two commands and several weeks of
    them: `cycle_ms` was added after the 2026-09-01 evening was recorded, and the writer
    serialises with `default=str`, so a field's *type* is not guaranteed either.
    """

    #: 1-based line number in the file, oldest = 1. Paging must not cost a row its
    #: identity — the audit that found the three bidder defects cites line numbers.
    index: int
    at_ms: int | None = None
    node: str | None = None
    lot: str | None = None
    name: str | None = None
    price: float | None = None
    walk_away: float | None = None
    provenance: str | None = None
    decision: str | None = None
    reason: str | None = None
    credits_left: float | None = None
    max_cap: float | None = None
    #: The count, not the 27 ids: the list is the rosa and the row is a decision.
    owned_count: int | None = None
    cycle_ms: float | None = None


class JournalPage(BaseModel):
    ok: bool
    #: Absolute, and the point of the empty state: `fantabot_data_dir` is relative and
    #: resolves against the launcher's working directory, so "no journal yet" and "you are
    #: looking in the wrong place" are the same screen until it says where it looked.
    path: str
    exists: bool
    total: int = 0
    #: Lines that did not parse. A torn tail is one, and it is reported rather than
    #: silently dropped — a number that moves is how a truncated file announces itself.
    skipped: int = 0
    offset: int = 0
    limit: int = 0
    rows: list[JournalRow] = []
    error: str | None = None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _journal_row(index: int, raw: dict[str, Any]) -> JournalRow:
    owned = raw.get("owned")
    return JournalRow(
        index=index,
        at_ms=_as_int(raw.get("at_ms")),
        node=_as_text(raw.get("node")),
        lot=_as_text(raw.get("lot")),
        name=_as_text(raw.get("name")),
        price=_as_float(raw.get("price")),
        walk_away=_as_float(raw.get("walk_away")),
        provenance=_as_text(raw.get("provenance")),
        decision=_as_text(raw.get("decision")),
        reason=_as_text(raw.get("reason")),
        credits_left=_as_float(raw.get("credits_left")),
        max_cap=_as_float(raw.get("max_cap")),
        owned_count=len(owned) if isinstance(owned, list) else None,
        cycle_ms=_as_float(raw.get("cycle_ms")),
    )


def read_journal(
    path: Path, *, offset: int = 0, limit: int = DEFAULT_JOURNAL_LIMIT
) -> JournalPage:
    """One page of the journal at `path`, newest first (pure but for the one read).

    **A line that does not parse is skipped, not fatal** — the landing zone's rule, and
    for a sharper reason here: the journal flushes per line, so the only line a crash can
    tear is the last one, and tail-first that is the *first* row a viewer renders. A
    reader that raised on it would show nothing at all for the evening it exists to
    explain.

    The whole file is parsed to page it, because a JSONL has no index and the count is
    part of the answer. Measured against the 2026-09-01 evening — 1.6 MB, 5,192 rows,
    0 skipped — at **29.8 ms**, well inside the 2 s budget T1 set for a synchronous
    panel read, so this stays a read and does not become a job.
    """
    limit = max(1, min(limit, MAX_JOURNAL_LIMIT))
    offset = max(0, offset)
    shown = str(path)
    if not path.exists():
        return JournalPage(ok=True, path=shown, exists=False, offset=offset, limit=limit)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:  # a directory, a permission, a vanished volume
        return JournalPage(
            ok=False,
            path=shown,
            exists=True,
            offset=offset,
            limit=limit,
            error=type(exc).__name__,
        )

    parsed: list[tuple[int, dict[str, Any]]] = []
    skipped = 0
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(decoded, dict):
            parsed.append((number, decoded))
        else:
            skipped += 1

    window = parsed[::-1][offset : offset + limit]
    return JournalPage(
        ok=True,
        path=shown,
        exists=True,
        total=len(parsed),
        skipped=skipped,
        offset=offset,
        limit=limit,
        rows=[_journal_row(number, raw) for number, raw in window],
    )


@router.get("/asta/journal", response_model=JournalPage, tags=["asta"])
def asta_journal(offset: int = 0, limit: int = DEFAULT_JOURNAL_LIMIT) -> JournalPage:
    """The CLI's record of a live room, read back. The app writes nothing here.

    Resolved absolute deliberately: `fantabot_data_dir` defaults to `./data`, which is
    only the repository's `data/` when the process was started from the repository root.
    That is §3.1's footgun in `tasks/archive/fantalab-in-the-app-spec.md`, and it is *not*
    fixed here — moving the journal would move an artefact the CLI owns and the 2026-09-01
    audit was done against — so the screen says which file it read instead of implying
    there is only one.
    """
    from fantabot.config import settings

    path = (Path(settings.fantabot_data_dir) / JOURNAL_FILE).resolve()
    return read_journal(path, offset=offset, limit=limit)
