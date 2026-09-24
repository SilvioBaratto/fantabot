"""Asta plan — the optimal roster for a lega, on its real (snapshotted) roster rules.

**It calls the CLI's planner rather than mirroring it.** It used to assemble its own
inputs and they had drifted in ten of them (the archived parity-phase spec, §11.1).
Three were wrong rather than merely narrower: `sentiment=None` is not "no opinion" but the sentiment model's **ablation
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
and, until this endpoint, nothing read. Same page, per §7 of the archived
fantalab-in-the-app-phase spec: the room check and the journal are sections of the Asta
page rather than a tenth nav entry.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
from fantabot.adapters.files.room_journal import read_rows

# Module-level, not lazy inside the route, and deliberately so: they are the three seams
# `test_asta_advisory_route.py` replaces, and a name imported inside the function body is one
# a `monkeypatch.setattr` on this module cannot reach. The fold itself is
# `application/asta_advisory`'s; these are the doors to it and to the ledger.
from fantabot.adapters.http.fantalab.feed import ledger_events
from fantabot.adapters.http.fantalab.listone import fetch as listone_fetch
from fantabot.application.asta_advisory import build_advisory
from fantabot.application.plan_request import (
    DEFAULT_LAM,
    DEFAULT_NUM_CREDITS,
    DEFAULT_NUM_TEAMS,
    WALK_AWAY_UNPRICED,
)
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter()


#: How many plan members get a walk-away. **An explicit bound, never `None`.**
#:
#: The archived parity-phase plan says "choose the target count deliberately and say so in
#: the docstring **rather than defaulting to `None`**", and an earlier version of this file
#: set exactly the
#: sentinel it forbade. 40 sits above the largest roster the platform has declared (32, per
#: the 2026-09-02 settings drift), so in practice the whole plan is priced — but it is a
#: stated ceiling, so a pool that grew past it would truncate *visibly*.
#:
#: **Cost, measured** on the pinned 548-player pool (empty roster, `lam=0`, 30 targets):
#: `reservations` 0.10 s against `lot_reference`+`lot_ceiling` at **1.31 s** — 12.7x, for the
#: number that is actually in credits. The page is read once per lega selection; a room is a
#: different budget and prices one lot per cycle. If a larger pool makes 1.3 s too much,
#: lower this — truncating is honest, because a row that was not priced says so.
PAGE_WALK_AWAY_TARGETS = 40

#: Only reachable if a plan ever exceeded that bound. Its own string, so a truncated row
#: cannot be read as an owned one.
WALK_AWAY_NOT_SHOWN = f"not shown: beyond the top {PAGE_WALK_AWAY_TARGETS} by ceiling"


class PlanPlayer(BaseModel):
    player_id: str
    nome: str
    #: The observed **mean clearing price** for this player across the recorded corpus of
    #: this league shape. It is what the market paid, not what he is worth to us — and for
    #: as long as this page has existed it was the only number on it, under the heading
    #: "Price", which reads as advice.
    price: float
    #: The *prezzo di rinuncia*, **in credits** — `lot_reference` + `lot_ceiling`, the pair
    #: the live room prices a lot with (Task 1.3).
    #:
    #: It was `reservations`' marginal until 1.12: an **objective** difference clamped by the
    #: budget and never converted to credits, which `SPEC.md` §2.A calls the unit error by
    #: name. Measured on the live pool — Calhanoglu 140.5 against a corpus price of 71.8, and
    #: five of thirty plan members at exactly 0.0, one beside a corpus price of 96.2.
    #:
    #: `None` means not priced, never zero: a zero here is a real answer ("a substitute
    #: exists") carrying `WALK_AWAY_HOLD`. Rendering the two alike is defect B2 restated.
    walk_away: int | None = None
    walk_away_provenance: str = WALK_AWAY_UNPRICED


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
    #: One of `api/outcomes.ASTA_PLAN_OUTCOMES`. `found` is kept beside it because the
    #: frontend and three tests read it, and because "did I get a plan" is a question worth
    #: answering without a string comparison — but the *screen* is chosen by this.
    outcome: str = "planned"
    #: Why not, and what to do about it. Empty only on `planned`.
    reason: str | None = None
    listone: str = ""
    roster_size: int = 0
    #: One of the three constants in `domain/asta/state`: `SNAPSHOT_DECLARED`,
    #: `ROOM_DECLARED` or `ASSUMED_NOTHING`. Beside the size, never behind a hover — the
    #: room-check panel's rule, and for its reason: a band nobody declared and a band the
    #: lega stated are different facts, and only one is worth planning on.
    roster_provenance: str = ""
    total_cost: float = 0.0
    objective: float = 0.0
    budget: float = 0.0
    #: The knobs the CLI exposes as options, echoed so the page says what it planned on.
    #: `lam` was fixed at 0.0 and untunable; `owned` did not exist, so every plan the page
    #: has ever shown was for an empty roster. Both are query parameters now, and `lam`'s
    #: default is the CLI's — `application.plan_request.DEFAULT_LAM`.
    #:
    #: The `0.0` still on this line is **this model's** default, not the route's, and it is
    #: reached only by the `found=False` screens, which carry no plan to have planned on. A
    #: planned response always overwrites it with what was asked for.
    #:
    #: It stays `0.0` deliberately, and `test_parity_asta_defaults.py` pins it there so the
    #: next sweep for stray `lam = 0.0` does not "finish the job" here. This field *echoes*
    #: what a plan was solved at; it never decides anything. On a `found=False` response
    #: nothing was solved, so `0.3` would be a claim — a risk aversion the page could print
    #: beside a failure that never reached the optimizer — where `0.0` is the same "nothing
    #: here" that `total_cost`, `objective` and `budget` already say one line above.
    #:
    #: Measured: with this at `0.0` and the route at `DEFAULT_LAM`, deleting `lam=lam,` from
    #: the planned return below fails `test_parity_asta_plan.py::
    #: test_the_page_says_what_it_planned_on` with `assert 0.0 == 0.3`. Align the two and
    #: that deletion survives — the page would echo the route's default whether or not the
    #: plan was ever told it. The difference is what makes the echo observable.
    lam: float = 0.0
    owned: list[str] = []
    #: `None` means "not narrowed" — the listone was unreachable and the plan degraded
    #: open. Never `0`: an empty exclusion set and an unknown one are different facts.
    callable_pool: int | None = None
    players: list[PlanPlayer] = []
    fallbacks: list[Fallback] = []


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
    # `DEFAULT_LAM`, never a literal. The page sends no `lam` at all
    # (`core/api/asta.service.ts:15` sends the lega and nothing else), so this default *is*
    # the objective every plan the operator ever sees is solved against — and while it read
    # `0.0` the page solved `sum(mu)` for a lega whose `asta optimize` solved
    # `sum(mu) - 0.3*Var`. Two rosters, one lega, on the pair CLAUDE.md names by name.
    lam: float = DEFAULT_LAM,
    fallbacks: int = 3,
    owned: str = "",
    teams: int = DEFAULT_NUM_TEAMS,
    credits: int = DEFAULT_NUM_CREDITS,
) -> AstaPlan:
    """The optimal roster for a lega, built from the same request the CLI builds.

    `fallbacks` and `lam` default to the CLI's defaults — 3, and
    `application.plan_request.DEFAULT_LAM` — and `owned` exists at all: the page has only
    ever shown the plan for an empty roster, which is the right answer on the morning of the
    asta and the wrong one on every evening after it. `lam` is read from the shared constant
    rather than restated, because restating it is how it came to be 0.0 here against 0.3 on
    the command; `api/tests/parity/test_parity_asta_defaults.py` pins the two defaults.

    `teams`/`credits` name the recorded corpus cell to price against — a riparazione or a
    friend's league is a different shape, and pricing it off ours would be somebody else's
    game. An unrecorded shape comes back `no_corpus` listing the shapes that *are* recorded.

    **Latency, re-measured after 1.12** on the live 529-player narrowed Mantra pool
    (2026-09-07, `lam=0`, empty roster, warm database): the plan is **0.12 s** and pricing
    all thirty walk-aways adds **1.23 s** — **1.35 s end to end**, inside the 2 s budget T1
    set for a synchronous panel read, so this stays a read and does not become a job. Those
    numbers were taken at `lam=0`, which was this route's default then and is not now; the
    default path is `DEFAULT_LAM` and the figures are a record of a measurement, not a
    claim about today's default.

    The numbers this docstring carried before (0.07 s for thirty, 0.12 s total) were
    `reservations`' and are gone with it: that call was 12.7x cheaper and returned an
    objective difference rather than credits. The cost is the price of the right number, and
    the margin is now 1.5x rather than 17x — `PAGE_WALK_AWAY_TARGETS` is the knob if a larger
    pool erodes it, and capping is honest because a row that was not priced says so in its
    provenance.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.application import lega_reads as reads
    from fantabot.application.plan_request import (
        EmptyPool,
        NoSentimentRows,
        PlanRequest,
        build_plan,
        callable_ids,
        walk_aways,
    )
    from fantabot.domain.asta.optimizer import InfeasibleRoster
    from fantabot.domain.asta.prices import NoCorpus
    from fantabot.domain.asta.report import parse_ids
    from fantabot.domain.asta.sentiment import SentimentWeights
    from fantabot.domain.asta.state import AstaState

    from fantabot_app.api.outcomes import because

    # Degrades open to `None`, never to an empty set: `read_plan_inputs` reads an empty
    # collection as a total exclusion and would empty the pool. The warning is dropped
    # rather than logged per request — `callable_pool` on the response is how the page
    # says whether the narrowing happened.
    narrowed = callable_ids(warn=lambda _note: None)

    # The order below is the order an operator would ask the questions, and it is
    # load-bearing for the same reason `endpoints/room.py`'s is: the first live probe there
    # answered "not a link" with a decryption failure. Reaching the database comes first
    # because nothing else can be established without it; the lega comes next because
    # without its snapshot every input after it is a guess.
    try:
        with database_manager.get_session() as session:
            snapshot = reads.latest_settings(session, league_id)
            if snapshot is None:
                # Not a failure the planner reports — the planner would happily run. With
                # no snapshot the format, the budget and the roster band are all defaults,
                # so a plan here is three guesses wearing an answer's clothes.
                return AstaPlan(
                    found=False,
                    outcome="no_lega",
                    reason=(
                        f"lega {league_id} has never been synced, so its format, budget and "
                        "roster band are unknown. Run `fantabot lega sync --write`."
                    ),
                )

            budget = float(snapshot.budget) if snapshot.budget else 500.0
            # One reader for "what band does this lega play", shared with the CLI since 2.1.
            # This route had its own, Mantra-only, falling back to a bare
            # `ClassicRosterRules()` even when the snapshot carried the band.
            rules, provenance, fmt = reads.rules_for_league(session, league_id)

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
                # The recorded shape, defaulting to our own 8x500 and **settable** since
                # 1.17. It used to pass `int(budget)` with `num_teams` pinned at 8, which
                # addresses a cell nobody chose — three such cells are empty in the live
                # corpus while the same budget has thousands of sales at another team count.
                # 1.6 gave `asta optimize` its flags and left this route hardcoded, which is
                # the same "explicit in code, unreachable to the operator" one surface along:
                # an operator could not price a riparazione or a friend's league. An
                # unrecorded shape is `no_corpus` below, never a nearest-shape guess.
                num_teams=teams,
                num_credits=credits,
            )
            planned = build_plan(session, request)

            # The walk-away, per target — two re-solves each. See `walk_aways` for why the
            # marginal it replaces was the wrong number in the wrong unit.
            #
            # Inside the `try`, deliberately. The rule this route keeps is *fail closed on a
            # decision*, and a re-solve over the world `build_plan` just solved can only fail
            # for a reason neither anticipated. Catching it here to render the plan without
            # the column would report every player as unpriced — a false statement rather
            # than a missing one, and 1.7's guard catches the attempt.
            state = AstaState(owned=tuple(sorted(request.owned)), total_budget=budget)
            priced = walk_aways(state, planned.world, planned.result.optimal,
                                rules=rules, lam=lam)
            if len(priced) > PAGE_WALK_AWAY_TARGETS:
                # Stated, not silent: a bound that truncates without saying so reads as
                # "we priced everything".
                keep = set(
                    sorted(priced, key=lambda p: -priced[p].credits)[:PAGE_WALK_AWAY_TARGETS]
                )
                priced = {p: w for p, w in priced.items() if p in keep}
    except NoSentimentRows as exc:
        return AstaPlan(found=False, outcome="no_sentiment", reason=str(exc))
    except NoCorpus as exc:
        return AstaPlan(found=False, outcome="no_corpus", reason=str(exc))
    except EmptyPool as exc:
        return AstaPlan(found=False, outcome="empty_pool", reason=str(exc))
    except InfeasibleRoster as exc:
        # The rosa cannot be seeded at all. A different screen from an empty pool: there
        # are players, and no legal eleven among them.
        return AstaPlan(found=False, outcome="infeasible", reason=str(exc))
    except (SQLAlchemyError, OSError) as exc:
        # "We could not ask": the database would not open, a migration is missing, a socket
        # died. Named rather than caught bare — anything outside these families is a bug in
        # this repository and reaches FastAPI as a 500, which is louder than a tidy page.
        # Typed and one line, because a traceback on a page says the call failed and not
        # which of five things failed.
        return AstaPlan(found=False, outcome="unreachable", reason=because(exc))

    world = planned.world
    return AstaPlan(
        found=True,
        outcome="planned",
        listone=fmt,
        roster_size=int(getattr(rules, "size", len(planned.result.optimal.player_ids))),
        roster_provenance=provenance,
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
                walk_away=(
                    None
                    if pid not in priced or priced[pid].provenance == WALK_AWAY_UNPRICED
                    else priced[pid].credits
                ),
                walk_away_provenance=(
                    priced[pid].provenance if pid in priced else WALK_AWAY_NOT_SHOWN
                ),
            )
            for pid in planned.result.optimal.player_ids
        ],
        fallbacks=[
            Fallback(total_cost=float(f.total_cost), objective=float(f.objective))
            for f in planned.result.fallbacks
        ],
    )


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
    #: Where to resume a **follow** from — sent straight back as `?since=`, the idiom
    #: `GET /jobs/{id}`'s `next_index` already uses in this app. Zero in page mode, and
    #: deliberately so: a page does not tail, and one field with two meanings is how a
    #: paging client comes to send back a position that means something else.
    #:
    #: It is the last row **parsed**, never the file's line count. The journal flushes
    #: per line, so a line caught mid-flush is skipped and counted this poll and parses
    #: the next one — a position past it would drop that cycle from the evening's only
    #: record, silently.
    next_index: int = 0
    rows: list[JournalRow] = []
    error: str | None = None


def read_journal(
    path: Path,
    *,
    offset: int = 0,
    limit: int = DEFAULT_JOURNAL_LIMIT,
    follow: bool = False,
    since: int = 0,
) -> JournalPage:
    """One page of the journal at `path`, newest first — or, with `follow`, its tail.

    The parsing is `read_rows`'; what is left here is the page — bounds, the window, and
    the three states a screen has to tell apart. **"Missing" and "unreadable" are not the
    same answer**: rendering a directory-where-a-file-should-be as "no journal yet" sends
    the operator looking for a path that is already right.

    **`follow` reverses the order as well as the window, and that is the point.** A page
    is read from the end of an evening and so arrives newest first; a tail is appended to
    a list on a screen while the room is still running, and a viewer that had to reverse
    each response before appending it would draw the evening inside out every two seconds.

    **Two parameters rather than the one the plan called for**, and the second is named
    `since` rather than reusing `offset`. They mean different things — `offset` is how
    many rows to skip from the newest, `since` is a row's own 1-based line number in the
    file — and this repository has already paid for one name carrying two meanings inside
    one body. A client that sent its tail position back as `offset` would page from the
    wrong end and be told nothing was wrong.

    The whole file is parsed either way: a JSONL has no index, and `read_rows` is the one
    reader both surfaces share. 29.8 ms against the recorded 5,192-row evening, well
    inside a 2 s poll.
    """
    limit = max(1, min(limit, MAX_JOURNAL_LIMIT))
    offset = max(0, offset)
    since = max(0, since)
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

    if follow:
        # `read_rows` is newest first; a tail is not. Reversed here rather than given a
        # second ordering in the adapter — the file's order is one decision and it is
        # stated there once.
        oldest_first = tuple(reversed(rows))
        fresh = [entry for entry in oldest_first if entry.index > since]
        window = tuple(fresh[:limit])
        # Clamped to the last row the file actually holds when nothing is new, for
        # `JobRegistry.lines_since`' reason: a position the file cannot reach is a client
        # that never sees another row, and it says nothing while it waits.
        last_parsed = oldest_first[-1].index if oldest_first else 0
        next_index = window[-1].index if window else min(since, last_parsed)
    else:
        window = tuple(rows[offset : offset + limit])
        next_index = 0

    return JournalPage(
        ok=True,
        path=shown,
        exists=True,
        total=len(rows),
        skipped=skipped,
        offset=offset,
        limit=limit,
        next_index=next_index,
        # Field for field, deliberately. `JournalRow` is `JournalEntry` with a response
        # model's docstrings on it, and a hand-written mapping between the two is the
        # seam the three dropped keys came through.
        rows=[JournalRow(**dataclasses.asdict(entry)) for entry in window],
    )


@router.get("/asta/journal", response_model=JournalPage, tags=["asta"])
def asta_journal(
    offset: int = 0,
    limit: int = DEFAULT_JOURNAL_LIMIT,
    follow: bool = False,
    since: int = 0,
) -> JournalPage:
    """The CLI's record of a live room, read back. The app writes nothing here.

    `?follow=1&since=N` tails it instead: the rows written after line N, oldest first,
    and a `next_index` to send back. That is what a watch job's viewer polls — the
    journal is the only channel a supervised child has to a screen, because its own
    stdout is a Rich `Live` that renders to a pipe nobody reads.

    Resolved absolute deliberately: `fantabot_data_dir` defaults to `./data`, which is
    only the repository's `data/` when the process was started from the repository root.
    That is §3.1's footgun in the archived fantalab-in-the-app-phase spec, and it is *not*
    fixed here — moving the journal would move an artefact the CLI owns and the 2026-09-01
    audit was done against — so the screen says which file it read instead of implying
    there is only one.
    """
    from fantabot.config import journal_path

    return read_journal(
        journal_path(), offset=offset, limit=limit, follow=follow, since=since
    )


# -- the rolling advisory, over a live room's sale ledger ---------------------------------


class AdvisoryTargetOut(BaseModel):
    player_id: str
    nome: str
    walk_away: int
    #: False when the walk-away is under one credit. `reservations` clamps a negative
    #: marginal to zero — which means only that he is freely replaceable — and the bidder
    #: refuses at every price, because its smallest raise is `current + step`. A screen
    #: saying "chase, walk-away 0" names the one thing the system will not do. He stays on
    #: the list: he is in the target roster and the operator should see him.
    chase: bool


class AdvisoryOpponentOut(BaseModel):
    team_id: str
    players: int
    spent: int
    remaining: int


class AstaAdvisory(BaseModel):
    outcome: str
    reason: str = ""
    targets: list[AdvisoryTargetOut] = []
    opponents: list[AdvisoryOpponentOut] = []
    #: Sales folded — every one the listone could name.
    sales: int = 0
    #: Sales it could not. Each is a purchase nobody subtracted, so a rival's budget and
    #: that player's availability are both wrong until it is explained. On the response
    #: rather than in a log, because only the screen can explain it.
    dropped_sales: int = 0
    total_cost: int = 0
    objective: float = 0.0


@router.get("/asta/advisory", response_model=AstaAdvisory, tags=["asta"])
def asta_advisory(
    league: str,
    db: int,
    team: str,
    # **Required, not defaulted.** It carried `= "mantra"` and was safe only because
    # `asta.ts` happens to guard it — the page refuses to ask for an advisory until its room
    # check produced an `asta_type`, because "an advisory priced against another lega's game
    # is worse than none". Any other caller got the guess, and nothing downstream can raise:
    # the pool, the prices and the listone bridge are all legal for the wrong game, so the
    # answer is exit 200 and a complete advisory for a different sport. `asta live --league`
    # had the identical default and is fixed in the same commit.
    listone: Literal["mantra", "classic"],
    season: str = "2026/27",
    budget: float = 500.0,
    # The same shared default, for the same reason one route along: `asta.service.ts`'s
    # `advisory()` sends no `lam` either, and the command this route mirrors is
    # `asta live --league`, whose `--lam` is `DEFAULT_LAM`. A literal here would have this
    # page advising on `sum(mu)` while the operator's own live view advised on
    # `sum(mu) - 0.3*Var` — the `/asta/plan` split, on the surface that is watched during
    # the auction rather than before it.
    lam: float = DEFAULT_LAM,
    teams: int = DEFAULT_NUM_TEAMS,
    credits: int = DEFAULT_NUM_CREDITS,
) -> AstaAdvisory:
    """The target roster after every sale so far, with a walk-away each, and the rivals.

    **Unauthenticated, as `asta live --league --db`'s own ledger read is.** The
    `purchases/<fl>` ledger is on the open RTDB (docs/fantalab/06 §10) and needs only the
    shard, so this route takes the shard and our team id rather than resolving the room. A
    route that resolved would need a FantaLab session for a read that does not, and would
    answer `no_credential` to a question about a ledger.

    ⚠ The CLI half of that sentence now has a caveat. `asta live --league` still reads the
    ledger unauthenticated, but it *additionally* probes the room for its `asta_type` and
    degrades to the recorded corpus, and then to `--format`, when it cannot. This route does
    not probe — it takes `listone` as a required parameter instead, because the page already
    holds the room check's answer and a route that re-fetched it would need the session this
    paragraph exists to avoid.

    `teams`/`credits` name the recorded corpus cell to price against, exactly as
    `GET /asta/plan` does — a 10x650 room priced off our 8x500 corpus is somebody else's
    game, and with no corpus at all the budget constraint is vacuous.

    The fold, the id resolution and the world read are `application/asta_advisory`'s; this
    is a serialiser and a choice of screen per outcome. `--replay` is deliberately absent:
    the archived parity-phase spec's T20 keeps it as developer machinery, and it is the
    one input this surface has no way to hand over.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.application.asta_advisory import AdvisoryRequest
    from fantabot.application.plan_request import EmptyPool, NoSentimentRows
    from fantabot.domain.asta.optimizer import InfeasibleRoster
    from fantabot.domain.asta.prices import NoCorpus

    from fantabot_app.api.outcomes import because

    try:
        events = ledger_events(db, league)
        bridge = listone_fetch()
        with database_manager.get_session() as session:
            advisory = build_advisory(
                session,
                AdvisoryRequest(
                    our_team_id=team,
                    season=season,
                    # The room's own `asta_type`, which the page takes from its room check.
                    # It selects both the pool and the corpus: a Classic room advised as
                    # Mantra is headed by players it cannot call, priced off another game.
                    listone=listone,
                    as_of=_today(),
                    budget=budget,
                    lam=lam,
                    num_teams=teams,
                    num_credits=credits,
                ),
                events=events,
                bridge=bridge,
            )
    except NoSentimentRows as exc:
        return AstaAdvisory(outcome="no_sentiment", reason=str(exc))
    except NoCorpus as exc:
        return AstaAdvisory(outcome="no_corpus", reason=str(exc))
    except EmptyPool as exc:
        return AstaAdvisory(outcome="empty_pool", reason=str(exc))
    except InfeasibleRoster as exc:
        # The rosa cannot be seeded at all — a different screen from an empty pool: there
        # are players, and no legal eleven among them.
        return AstaAdvisory(outcome="infeasible", reason=str(exc))
    except (SQLAlchemyError, OSError, httpx.HTTPError, ValueError) as exc:
        # "We could not ask." Named rather than caught bare: anything outside these
        # families is a bug in this repository and reaches FastAPI as a 500, which is
        # louder than a tidy page.
        #
        # ⚠ **`httpx.HTTPError` is here because this is the first route in this module that
        # reads the network, and `rtdb` is not `apileague`.** `apileague._send` maps httpx
        # onto `ApiTimeout`/`ApiUnavailable`; `rtdb.read_snapshot` does not, and
        # `httpx.HTTPError` inherits from `Exception` directly — not from `OSError`. So a
        # ledger that would not answer escaped as a 500 on exactly the failure this route
        # pins as `unreachable`, and the page replaced the server's sentence with its own
        # generic one. `ValueError` covers the malformed body (`json.JSONDecodeError`).
        return AstaAdvisory(outcome="unreachable", reason=because(exc))

    return AstaAdvisory(
        outcome="advised",
        targets=[
            AdvisoryTargetOut(
                player_id=t.player_id, nome=t.nome, walk_away=t.walk_away, chase=t.chase
            )
            for t in advisory.targets
        ],
        opponents=[
            AdvisoryOpponentOut(
                team_id=o.team_id, players=o.players, spent=o.spent, remaining=o.remaining
            )
            for o in advisory.opponents
        ],
        sales=advisory.sales,
        dropped_sales=advisory.dropped_sales,
        total_cost=advisory.total_cost,
        objective=advisory.objective,
    )
