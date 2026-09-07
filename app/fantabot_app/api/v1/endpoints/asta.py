"""Asta plan — the optimal roster for a lega, on its real (snapshotted) roster rules.

**It calls the CLI's planner rather than mirroring it.** It used to assemble its own
inputs and they had drifted in ten of them (`SPEC.md` §11.1). Three were wrong rather than
merely narrower: `sentiment=None` is not "no opinion" but the sentiment model's **ablation
control** — plain `fvm`, which on the 2026-08-28 data chases a player with a metatarsal
fracture to 62 credits — so the page was showing an operator the control arm of an
experiment as advice; `tilt_k=1.0` was four times the CLI's and inert only because there
was nothing to tilt; and no `callable_ids` meant a 30-man rosa could carry a slot the
evening was unable to fill, 41 of 570 pool players being absent from FantaLab's listone.

Both sides now build an `application.plan_request.PlanRequest` and hand it to `build_plan`.
`api/tests/parity/` pins the two requests against each other.

Two inputs the app is still *righter* about, and they stay: the roster rules come from the
lega's own snapshot rather than a hardcoded 30 (2.1 gives the CLI the same), and the format
is derived from `role_groups` rather than typed. Read-only.

It also serves the **room journal** — `data/room_journal.jsonl`, which the CLI writes
and, until this endpoint, nothing read. Same page, per §7 of the archived phase spec
(`tasks/archive/fantalab-in-the-app-spec.md`): the room check and the journal are sections
of the Asta page rather than a tenth nav entry.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import Any

from fantabot.adapters.files.room_journal import read_rows
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class PlanPlayer(BaseModel):
    player_id: str
    nome: str
    price: float


class Fallback(BaseModel):
    """A next-best plan, one per top target lost. The CLI prints three; so does the page.

    Rendered because a single optimal rosa reads as a prescription, and it is not one: the
    evening will take players off the board and the operator needs to know what the plan
    becomes when it does.
    """

    total_cost: float
    objective: float


class AstaPlan(BaseModel):
    found: bool
    #: Why not, when `found` is false and the reason is one this endpoint can name. The
    #: full pinned tuple of outcomes across the three decision routes is 1.7; this is the
    #: two that would otherwise turn "run `news fetch` first" into a blank screen.
    reason: str | None = None
    listone: str = ""
    roster_size: int = 0
    total_cost: float = 0.0
    objective: float = 0.0
    budget: float = 0.0
    #: The knobs the CLI exposes as options, echoed so the page says what it planned on.
    #: `lam` was fixed at 0.0 and untunable; `owned` did not exist, so every plan the page
    #: has ever shown was for an empty roster.
    lam: float = 0.0
    owned: list[str] = []
    #: `None` means "not narrowed" — the listone was unreachable and the plan degraded
    #: open. Never `0`: an empty exclusion set and an unknown one are different facts.
    callable_pool: int | None = None
    players: list[PlanPlayer] = []
    fallbacks: list[Fallback] = []


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


def _today() -> date:
    """The one calendar read on the app's asta path. The parity tier patches exactly this.

    A seam rather than an inline `date.today()`, for `interface/asta._today`'s reason:
    `domain/asta/sentiment.py` decays confidence on a 7-day half-life against `as_of`, and
    every stored row shares one `data_run` — so one day of drift rescales every reading and
    changes roster *membership*, not just a printed number. Two reads would be two things a
    harness must freeze in lockstep, and the one it misses is the one that expires it.
    `tests/domain/asta/test_asta_clock.py` counts them.
    """
    return date.today()  # noqa: DTZ011 — a local date, exactly as the CLI's seam reads it


@router.get("/asta/plan", response_model=AstaPlan, tags=["asta"])
def asta_plan(
    league_id: int,
    season: str = "2026/27",
    lam: float = 0.0,
    fallbacks: int = 3,
    owned: str = "",
) -> AstaPlan:
    """The optimal roster for a lega, built from the same request the CLI builds.

    `fallbacks` defaults to 3, the CLI's default, and `owned` exists at all — the page has
    only ever shown the plan for an empty roster, which is the right answer on the morning
    of the asta and the wrong one on every evening after it.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.application.plan_request import (
        DEFAULT_NUM_CREDITS,
        DEFAULT_NUM_TEAMS,
        EmptyPool,
        NoSentimentRows,
        PlanRequest,
        build_plan,
        callable_ids,
    )
    from fantabot.domain.asta.prices import NoCorpus
    from fantabot.domain.asta.report import parse_ids
    from fantabot.domain.asta.sentiment import SentimentWeights
    from fantabot.domain.classic.state import ClassicRosterRules

    from fantabot_app.api.reads import league as reads

    # Degrades open to `None`, never to an empty set: `read_plan_inputs` reads an empty
    # collection as a total exclusion and would empty the pool. The warning is dropped
    # rather than logged per request — `callable_pool` on the response is how the page
    # says whether the narrowing happened.
    narrowed = callable_ids(warn=lambda _note: None)

    try:
        with database_manager.get_session() as session:
            snapshot = reads.latest_settings(session, league_id)
            fmt = "classic" if (snapshot is not None and snapshot.role_groups == 1) else "mantra"
            budget = float(snapshot.budget) if snapshot and snapshot.budget else 500.0
            rules = ClassicRosterRules() if fmt == "classic" else build_roster_rules(snapshot)

            request = PlanRequest(
                season=season,
                listone=fmt,
                as_of=_today(),
                budget=budget,
                rules=rules,
                owned=frozenset(parse_ids(owned)),
                lam=lam,
                n_fallbacks=fallbacks,
                tilt_k=SentimentWeights().k,
                sentiment=True,
                sentiment_run=None,
                callable_ids=narrowed,
                # The recorded 8x500 shape, the same default the CLI uses. It used to pass
                # `int(budget)` with `num_teams` pinned at 8, which addresses a cell nobody
                # chose — three such cells are empty in the live corpus while the same
                # budget has thousands of sales at another team count. 1.6 makes every
                # caller state a shape and raises on an unrecorded one.
                num_teams=DEFAULT_NUM_TEAMS,
                num_credits=DEFAULT_NUM_CREDITS,
            )
            planned = build_plan(session, request)

        world = planned.world
        return AstaPlan(
            found=True,
            listone=fmt,
            roster_size=int(getattr(rules, "size", len(planned.result.optimal.player_ids))),
            total_cost=float(planned.result.optimal.total_cost),
            objective=float(planned.result.optimal.objective),
            budget=budget,
            lam=lam,
            owned=sorted(request.owned),
            callable_pool=None if narrowed is None else len(narrowed),
            players=[
                PlanPlayer(
                    player_id=pid,
                    nome=world.names.get(pid, pid),
                    price=float(world.prices.get(pid, 0.0)),
                )
                for pid in planned.result.optimal.player_ids
            ],
            fallbacks=[
                Fallback(total_cost=float(f.total_cost), objective=float(f.objective))
                for f in planned.result.fallbacks
            ],
        )
    # Two named refusals, said out loud. Without them "sentiment is on and nobody has run
    # `news fetch`" and "the database is down" are the same blank screen.
    except NoSentimentRows as exc:
        return AstaPlan(found=False, reason=str(exc))
    except EmptyPool as exc:
        return AstaPlan(found=False, reason=str(exc))
    # An unrecorded corpus shape. Named rather than swallowed because the alternative is
    # not a slightly-off plan: with no prices the budget constraint is vacuous, and that
    # bought a 25-man rosa for 25 credits of 500 on 2026-09-05.
    except NoCorpus as exc:
        return AstaPlan(found=False, reason=str(exc))
    except Exception:  # noqa: BLE001 — 1.7 replaces this with the pinned tuple of outcomes
        return AstaPlan(found=False)


# -- the room journal ---------------------------------------------------------------------

#: Where the journal is, what a row holds, and how a torn line is counted are all
#: `adapters/files/room_journal.py`'s to say — beside the writer, once. This module had
#: its own copy of the last two and no share of the first, which is how it came to read a
#: schema three keys behind the one being written.
#:
#: Both `asta room` and `asta bid` append to the same file and neither marks a run
#: boundary, which is why the viewer pages a file and not an evening (the archived phase
#: spec §9 leaves that open on purpose — the marker would change the artefact the
#: 2026-09-01 audit was done against).
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
    #: Credits already gone on lots the plan never named, and the evening's ceiling for
    #: them. Both have been written since `44cfe89` — an ancestor of this viewer's own
    #: commit `86acb6c` — and were read by nothing until 1.2.
    bargain_spent: int | None = None
    bargain_allowance: int | None = None
    #: The exception type of a poll that raised. Without it a `waiting` row and an `error`
    #: row are the same row of nulls, and telling a skipped poll from a crash is
    #: `error_row`'s entire purpose.
    error: str | None = None
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


def read_journal(
    path: Path, *, offset: int = 0, limit: int = DEFAULT_JOURNAL_LIMIT
) -> JournalPage:
    """One page of the journal at `path`, newest first.

    The parsing is `read_rows`'; what is left here is the page — bounds, the window, and
    the three states a screen has to tell apart. **"Missing" and "unreadable" are not the
    same answer**: rendering a directory-where-a-file-should-be as "no journal yet" sends
    the operator looking for a path that is already right.
    """
    limit = max(1, min(limit, MAX_JOURNAL_LIMIT))
    offset = max(0, offset)
    shown = str(path)
    existed = path.exists()
    try:
        rows, skipped = read_rows(path)
    except OSError as exc:  # a directory, a permission, a vanished volume
        return JournalPage(
            ok=False,
            path=shown,
            exists=True,
            offset=offset,
            limit=limit,
            error=type(exc).__name__,
        )
    if not existed:
        return JournalPage(ok=True, path=shown, exists=False, offset=offset, limit=limit)

    window = rows[offset : offset + limit]
    return JournalPage(
        ok=True,
        path=shown,
        exists=True,
        total=len(rows),
        skipped=skipped,
        offset=offset,
        limit=limit,
        # Field for field, deliberately. `JournalRow` is `JournalEntry` with a response
        # model's docstrings on it, and a hand-written mapping between the two is the
        # seam the three dropped keys came through.
        rows=[JournalRow(**dataclasses.asdict(entry)) for entry in window],
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
    from fantabot.config import journal_path

    return read_journal(journal_path(), offset=offset, limit=limit)
