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


#: How many plan members get a walk-away. `None` prices every unowned one.
#:
#: **Chosen by measurement, and the measurement contradicted the plan.** T30 recorded the
#: cost as a reason to cap this: `reservations` is one extra roster solve per target, and
#: 325,538 Python calls at five against 1,508,707 at thirty is a 4.6x difference —
#: `tests/domain/asta/test_asta_cycle_cost.py` pins a 500,000 ceiling on the room's cycle
#: for exactly that reason. Measured end to end on the live 529-player pool
#: (2026-09-07, `lam=0`, empty roster): **0.02 s at five, 0.03 s at ten, 0.07 s for all
#: thirty.** The call count is real and the wall clock is not the problem.
#:
#: So the cap is `None`. A page is read top-down the night before, and twenty unpriced rows
#: are twenty the operator prices by hand — the number they bid against is the walk-away,
#: which the requirements doc calls *"la funzione centrale"*.
#:
#: The room's ceiling is untouched and stays where it is. That test measures a *cycle*, at
#: a 2 s poll for a whole evening; this is one request per lega selection. "The page is not
#: a room" is T30's own framing, and once measured it cuts this way.
PAGE_WALK_AWAY_TARGETS: int | None = None

#: Where a walk-away came from — beside the number, never behind a hover.
#:
#: `domain/asta/state.rules_for_room` is the precedent and its docstring is the argument:
#: *"a provenance an operator cannot grep for consistently is one they stop trusting"*. So
#: these are constants with exact strings, not prose composed per call site.
WALK_AWAY_MARGINAL = "marginal: objective without him, re-solved"
#: `reservations` prices only *unowned* plan members — a player already bought needs no
#: walk-away, and that is the only reason a row carries none now that the cap is `None`.
WALK_AWAY_UNPRICED = "not priced: already owned"
#: `reservations` caps a walk-away at the remaining budget and gives that whole figure to a
#: target whose removal leaves no completable roster. The two are not distinguishable from
#: the returned number, so the label says both rather than picking one and being wrong
#: about it — which is the sort of confident provenance `rules_for_room` warns against.
WALK_AWAY_AT_BUDGET = "the whole remaining budget: no rosa without him, or the margin exceeds it"


class PlanPlayer(BaseModel):
    player_id: str
    nome: str
    #: The observed **mean clearing price** for this player across the recorded corpus of
    #: this league shape. It is what the market paid, not what he is worth to us — and for
    #: as long as this page has existed it was the only number on it, under the heading
    #: "Price", which reads as advice.
    price: float
    #: The *prezzo di rinuncia* — the most this rosa would pay before walking away. The
    #: requirements doc calls it *"la funzione centrale"*, and `lot_ceiling`,
    #: `lot_reference` and `reservations` had **zero call sites anywhere under `app/`**.
    #:
    #: `None` means not priced (see `PAGE_WALK_AWAY_TARGETS`), never zero: a walk-away of
    #: zero is a real answer meaning "a substitute exists at this price", and rendering the
    #: two the same is defect B2 restated — 4,501 of 5,192 journal rows carried a null
    #: walk-away and it read as a decision.
    walk_away: float | None = None
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


def _provenance(walk_away: float | None, at_budget: float) -> str:
    """Which of the three labels this number carries. Beside it, never behind a hover."""
    if walk_away is None:
        return WALK_AWAY_UNPRICED
    return WALK_AWAY_AT_BUDGET if walk_away >= at_budget else WALK_AWAY_MARGINAL


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

    **Latency, measured** on the live 529-player narrowed Mantra pool (2026-09-07,
    `lam=0`, empty roster, warm database): the plan is **0.10 s** and pricing all thirty
    walk-aways adds **0.07 s** — 0.12 s end to end, well inside the 2 s budget T1 set for a
    synchronous panel read, so this stays a read and does not become a job. Five targets
    would have cost 0.02 s and ten 0.03 s; the difference does not buy anything.
    `PAGE_WALK_AWAY_TARGETS` is the knob if a much larger pool ever changes that, and
    capping it is honest because an unpriced target says so in its provenance.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.application import lega_reads as reads
    from fantabot.application.plan_request import (
        DEFAULT_NUM_CREDITS,
        DEFAULT_NUM_TEAMS,
        EmptyPool,
        NoSentimentRows,
        PlanRequest,
        build_plan,
        callable_ids,
    )
    from fantabot.domain.asta.optimizer import InfeasibleRoster
    from fantabot.domain.asta.prices import NoCorpus
    from fantabot.domain.asta.report import parse_ids
    from fantabot.domain.asta.reservation import reservations
    from fantabot.domain.asta.sentiment import SentimentWeights
    from fantabot.domain.asta.state import AstaState
    from fantabot.domain.classic.state import ClassicRosterRules

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

            fmt = "classic" if snapshot.role_groups == 1 else "mantra"
            budget = float(snapshot.budget) if snapshot.budget else 500.0
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
                # budget has thousands of sales at another team count. An unrecorded shape
                # is `no_corpus` below, never a nearest-shape guess.
                num_teams=DEFAULT_NUM_TEAMS,
                num_credits=DEFAULT_NUM_CREDITS,
            )
            planned = build_plan(session, request)

            # The walk-away, per target: a second solve over the same world — see
            # `PAGE_WALK_AWAY_TARGETS` for why ten and not all of them.
            #
            # Inside the `try`, deliberately. The rule this route now keeps is *fail closed
            # on a decision*, and `reservations` solving the world that `build_plan` just
            # solved can only fail for a reason neither of them anticipated. Catching it
            # here to render the plan without the column would report every player as
            # "beyond the top ten", which is a false statement rather than a missing one.
            state = AstaState(owned=tuple(sorted(request.owned)), total_budget=budget)
            _, walkaways = reservations(
                state,
                planned.world.pool,
                value=planned.world.value,
                prices=planned.world.prices,
                teams=planned.world.teams,
                legality=planned.world.legality,
                rules=rules,
                lam=lam,
                n_targets=PAGE_WALK_AWAY_TARGETS,
            )
            at_budget = float(state.remaining_budget)
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
    except Exception as exc:  # noqa: BLE001 — the last named outcome, not a catch-all
        # Everything left is "we could not ask": the database would not open, a driver
        # failed, a migration is missing. Typed and one line, because a traceback on a page
        # says the call failed and not which of five things failed.
        return AstaPlan(found=False, outcome="unreachable", reason=because(exc))

    world = planned.world
    return AstaPlan(
        found=True,
        outcome="planned",
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
                walk_away=None if pid not in walkaways else float(walkaways[pid]),
                walk_away_provenance=_provenance(walkaways.get(pid), at_budget),
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
