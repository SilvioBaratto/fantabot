"""Lineup preview and submit — the best fieldable formation for a lega.

**`GET /lineup/plan` never submits. `POST /lineup/submit` is a dry run unless the request
asks to arm, and even then only if `FANTABOT_AUTO_ACT` is set.** Two locks, both named
separately when shut, and `arm` has **no default**: a request that does not say does not act.
That is not paranoia about a typo — it is the property that the operator who armed it is the
one watching, and a browser can be reloaded, restored by a session manager, or left open
overnight.

Both routes call `application/lineup_submit.py`, which is where the eight decisions on this
path live. `GET` used to hand-write the seven reads that build a plan, which is how the app
came to read the format from a different place than the command did.

Mirrors interface/lineup.py's plan path: my_team -> teamLineup_read -> lineup_settings ->
inputs_from_lineup -> plan_lineups, then returns the top PlannedLineup. This is the only
read that hits the live platform (apileague, bearer token), so it degrades open to a
reason when there is no key, no token, or no network — and it never calls
teamLineup_submit.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fantabot.adapters.http.apileague import teamLineup_read
from fantabot.application.arming import ARM, AUTO_ACT
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


@contextmanager
def _open_store() -> Any:
    """A `TokenStore` on an open session, and the one door out of `GET /lineup/current`.

    A seam rather than four lazy imports inside the route: it is what the route's own tests
    replace, and a name imported inside a function body is one a `monkeypatch.setattr` on
    this module cannot reach. No bearer enters this frame — `TokenStore` resolves it inside
    the adapter, which is `test_app_never_handles_a_plaintext_token`'s whole subject.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher

    with database_manager.get_session() as session:
        yield TokenStore(session, TokenCipher(settings.fantabot_encryption_key))


def _now() -> datetime:
    """The app's clock seam for the lineup path — one, so a harness has one target.

    `interface/lineup.py::_now` is the CLI's, and `tests/domain/asta/test_asta_clock.py`
    counts seams per surface. The value feeds `is_past_deadline`, which compares naive.
    """
    return datetime.now()  # noqa: DTZ005 — naive, as the platform's `mstr` is


class LineupPlayer(BaseModel):
    player_id: int
    nome: str


class LineupPlan(BaseModel):
    found: bool
    #: One of `api/outcomes.LINEUP_PLAN_OUTCOMES`. This route already carried a `reason`,
    #: which made it look solved — but every failure produced the *same* one, "Not
    #: connected, or no lineup available yet", so a network blip and a roster the platform
    #: refuses read identically and only one of them is worth waiting out.
    outcome: str = "planned"
    reason: str | None = None
    module: str = ""
    matchday: int | None = None
    #: Which competition this plan is for, resolved by `build_plans` rather than asked for.
    #: Carried because `GET /lineup/current` needs one and the page has no other way to
    #: learn it — and because "the plan" and "what is saved" are only comparable when both
    #: name the same competition. `build_plans` already returned it and it was discarded.
    competition: int | None = None
    starters: list[LineupPlayer] = []
    bench: list[LineupPlayer] = []


def build_lineup_plan(
    planned: Any, names: dict[int, str], competition: int | None = None
) -> LineupPlan:
    """Map a PlannedLineup + id->name dict to the response (pure)."""
    return LineupPlan(
        found=True,
        outcome="planned",
        module=planned.module,
        matchday=planned.mday,
        competition=competition,
        starters=[LineupPlayer(player_id=pid, nome=names.get(pid, str(pid))) for pid in planned.starts],
        bench=[LineupPlayer(player_id=pid, nome=names.get(pid, str(pid))) for pid in planned.bench],
    )


@router.get("/lineup/plan", response_model=LineupPlan, tags=["lineup"])
def lineup_plan(league_id: int) -> LineupPlan:
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_submit import build_plans
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import (
        ApiTimeout,
        ApiUnavailable,
        AppKeyRejected,
        TokenError,
        TokenRejected,
    )
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot_app.api.outcomes import because

    key = settings.fantabot_encryption_key
    if not key:
        return LineupPlan(
            found=False,
            outcome="no_credential",
            reason="No encryption key set — connect an account first.",
        )

    # The order is the order the questions arise, and it is load-bearing: a lega with no
    # stored token must not be reported as a network failure, and a network failure must
    # not be reported as a missing lineup.
    try:
        cipher = TokenCipher(key)
    except TokenError as exc:
        # `TokenCipher` raises `KeyMissing`/`KeyMalformed`, both `TokenError`. Named, so a
        # different failure in the constructor is a 500 and not a misleading "not connected".
        return LineupPlan(found=False, outcome="no_credential", reason=str(exc))

    try:
        with database_manager.get_session() as session:
            # The lifted builder, not a third copy of it. This route hand-wrote the same
            # seven reads — `my_team`, `competitions`, `teamLineup_read`, `lineup_settings`,
            # `roster_settings`, `inputs_from_lineup`, `plan_lineups` — which is how it came
            # to read the format from a different place than the command did.
            plans, names, comp = build_plans(TokenStore(session, cipher), league_id, 0)
    except (TokenRejected, AppKeyRejected) as exc:
        # The platform answered, and said no. A different fact from being unable to ask —
        # a rejected token is not going to resolve by reloading the page.
        return LineupPlan(found=False, outcome="refused", reason=str(exc))
    except (ApiTimeout, ApiUnavailable) as exc:
        return LineupPlan(found=False, outcome="unreachable", reason=str(exc))
    except TokenError as exc:
        # Nothing stored, stored under another key, expired, or for another lega. Each
        # says so in its own words and each names something the operator does by hand.
        return LineupPlan(found=False, outcome="no_credential", reason=str(exc))
    except (SQLAlchemyError, OSError) as exc:
        return LineupPlan(found=False, outcome="unreachable", reason=because(exc))

    if not plans:
        # There is a roster and the platform is reachable; no eleven of it is fieldable.
        # `plan_lineups` builds only on natural roles, so this means the rosa genuinely
        # cannot fill a module — not that the request failed.
        return LineupPlan(
            found=False,
            outcome="no_lineup",
            reason="No fieldable lineup for this lega yet — the rosa fills no allowed module.",
        )
    return build_lineup_plan(plans[0], names, comp)


#: Why a submit did not happen, as a screen. Same discipline as `api/outcomes.py`: a route
#: that returns one of a pinned tuple, compared for equality by a test.
#:
#: `not_armed` is a **success** as far as the request is concerned — a dry run is what the
#: caller asked for unless it said otherwise — so it carries the plan it would have sent.
SUBMIT_OUTCOMES = (
    "submitted",
    "not_armed",
    "no_matchday",
    "all_modules_refused",
    "no_credential",
    "refused",
    "unreachable",
)


class SubmitRequest(BaseModel):
    """**`arm` has no default on purpose.** A request that omits it is a 422, not a dry run.

    A default either way is a decision the last request makes for the next one, and the
    property being bought is that the operator who armed it is the one watching. It is never
    stored, never remembered across a reload, and never read from anywhere but this body.
    """

    league_id: int
    arm: bool
    competition: int = 0


class SubmitResult(BaseModel):
    outcome: str
    #: Empty on `submitted`. Names **every** shut lock on `not_armed`, not just the first.
    reason: str = ""
    #: The module that was sent, or — on a dry run — the one that would have been.
    module: str = ""
    matchday: int | None = None
    starters: list[LineupPlayer] = []
    bench: list[LineupPlayer] = []
    #: `True` only when a lineup actually reached the platform.
    submitted: bool = False
    #: The lineup **read back** afterwards: what the platform kept, not what we sent.
    saved_starters: int | None = None
    saved_at: str | None = None
    #: `(module, code)` for each module the platform refused before one stuck — the record
    #: of a `LUP009` walk-down, and empty on a first-try success.
    rejected: list[str] = []
    #: The `mstr` that looks past kickoff. A **warning** carried alongside a submit, never
    #: instead of one: `mstr` is not confirmed to be the lineup deadline.
    past_deadline: str | None = None
    #: Why the confirming read-back failed, when it did. Non-empty means the lineup reached
    #: the platform — `submitted` is `True` — and could not then be read back to prove it.
    #: The outcome deliberately stays `"submitted"`: a reader that does not know this field
    #: says "submitted", which is the true half, where a new outcome string would drop it
    #: into an unknown branch and lose that. An unknown is not a negative.
    unconfirmed: str = ""


#: What the app says when a lock is shut. The *fact* is shared with the CLI
#: (`application.arming`); the wording is local, because there is no `--arm` flag in an HTTP
#: request and a message naming one sends the reader to a terminal they are not using.
APP_SENTENCES = {
    ARM: "the request did not ask to arm",
    AUTO_ACT: "FANTABOT_AUTO_ACT is false",
}


@router.post("/lineup/submit", response_model=SubmitResult, tags=["lineup"])
def lineup_submit(request: SubmitRequest) -> SubmitResult:
    """Build the XI and submit it — **behind two locks, a dry run by default.**

    The eight decisions are `application/lineup_submit.submit_lineup`'s, the same ones
    `fantabot lineup submit` walks. This route chooses a screen for each.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_submit import (
        ALL_MODULES_REFUSED,
        NO_MATCHDAY,
        NOT_ARMED,
        submit_lineup,
    )
    from fantabot.config import settings
    from fantabot.domain.lineup.errors import LineupError
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import (
        ApiTimeout,
        ApiUnavailable,
        AppKeyRejected,
        TokenError,
        TokenRejected,
    )
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot_app.api.outcomes import because

    key = settings.fantabot_encryption_key
    if not key:
        return SubmitResult(
            outcome="no_credential",
            reason="No encryption key set — connect an account first.",
        )

    try:
        cipher = TokenCipher(key)
        with database_manager.get_session() as session:
            outcome = submit_lineup(
                TokenStore(session, cipher),
                league_id=request.league_id,
                competition=request.competition,
                arm=request.arm,
                now=_now,
            )
    except (TokenRejected, AppKeyRejected, LineupError) as exc:
        return SubmitResult(outcome="refused", reason=str(exc))
    except (ApiTimeout, ApiUnavailable) as exc:
        return SubmitResult(outcome="unreachable", reason=str(exc))
    except TokenError as exc:
        return SubmitResult(outcome="no_credential", reason=str(exc))
    except (SQLAlchemyError, OSError) as exc:
        return SubmitResult(outcome="unreachable", reason=because(exc))

    plan = outcome.plan
    body = SubmitResult(
        outcome="submitted",
        module=plan.module if plan else "",
        matchday=plan.mday if plan else None,
        starters=[
            LineupPlayer(player_id=pid, nome=outcome.names.get(pid, str(pid)))
            for pid in (plan.starts if plan else [])
        ],
        bench=[
            LineupPlayer(player_id=pid, nome=outcome.names.get(pid, str(pid)))
            for pid in (plan.bench if plan else [])
        ],
        rejected=[f"{module} ({code})" for module, code in outcome.rejected],
        past_deadline=outcome.past_deadline,
    )

    if outcome.refused == NO_MATCHDAY:
        return body.model_copy(
            update={
                "outcome": "no_matchday",
                "reason": (
                    "no matchday context for this competition — the lineup has no saved "
                    "coordinates yet. Try once the matchday opens."
                ),
            }
        )
    if outcome.refused == NOT_ARMED:
        # A dry run is not a failure: it is what was asked for. The plan travels with it.
        return body.model_copy(
            update={"outcome": "not_armed", "reason": outcome.arming.because(APP_SENTENCES)}
        )
    if outcome.refused == ALL_MODULES_REFUSED:
        return body.model_copy(
            update={
                "outcome": "all_modules_refused",
                "reason": "every fieldable module was refused by the platform.",
            }
        )

    return body.model_copy(
        update={
            "submitted": True,
            "unconfirmed": outcome.unconfirmed,
            "saved_starters": len(outcome.saved.get("starts", [])),
            "saved_at": str(outcome.saved.get("ldate")) if outcome.saved.get("ldate") else None,
        }
    )


# -- the scheduled history ----------------------------------------------------------------

#: How many runs one response carries by default, and at most.
DEFAULT_RUNS_LIMIT = 50
MAX_RUNS_LIMIT = 500

#: Older than this and the history says the scheduled job may not be running. The job fires
#: through the day and not overnight, so a normal night leaves roughly nine hours between runs;
#: twelve means a missed day, not a quiet night.
STALE_AFTER_HOURS = 12.0


class LineupRejectionRow(BaseModel):
    """One plan the walk did not keep — `lineup_runs.LineupRejection`, field for field."""

    module: str
    #: `LUP0xx`, or `GUARD <slot> <role> <cell>` for a plan our own guard skipped.
    code: str
    message: str = ""
    #: The XI that was refused, in the platform's slot order.
    starter_ids: list[int] = []
    starters: list[str] = []


class LineupShadowRow(BaseModel):
    """The other model's plan, logged beside the one sent — `lineup_runs.LineupShadow`."""

    model: str
    module: str
    starter_ids: list[int]
    bench_ids: list[int]
    starters: list[str]
    bench: list[str]
    #: E[league points], `3·P(W) + P(D)`: the objective.
    e_pts: float
    #: P(win), P(draw), P(loss).
    p_wdl: tuple[float, float, float]
    e_fp: float
    sd: float
    cuts: list[str] = []


class LineupRunRow(BaseModel):
    """One scheduled run — `adapters/files/lineup_runs.LineupRun`, field for field.

    Built with `LineupRunRow(**asdict(run))` rather than a hand-written mapping: that mapping
    is the seam the room journal's three dropped keys came through. That alone does not
    close it — pydantic *ignores* a key the model does not declare, so a field the writer
    gains is dropped here without a word. `test_lineup_runs_route` pins the field sets equal,
    for this row and both nested ones.
    """

    at: str
    league: int
    scheduled: bool
    #: `submitted`, `unconfirmed`, `skipped` or `failed` — decided once, by
    #: `application/lineup_submit.run_record`. This screen colours it and never re-derives it.
    status: str
    code: str = ""
    detail: str = ""
    module: str = ""
    matchday: int | None = None
    serie_a_matchday: int | None = None
    starters: list[str] = []
    bench: list[str] = []
    rejected: list[str] = []
    # --- record v2: each defaults to what a v1 line did not record ---
    #: The ids are what grading reads; the names above are for the eye.
    starter_ids: list[int] = []
    bench_ids: list[int] = []
    competition: int | None = None
    tid: int | None = None
    #: The model that chose what was sent. `""` on a v1 line, which never said.
    model: str = ""
    #: Why the named model was not the one used, when it was not.
    fallback: str = ""
    warnings: list[str] = []
    rejections: list[LineupRejectionRow] = []
    shadow: LineupShadowRow | None = None


class LineupRuns(BaseModel):
    ok: bool
    #: The file actually read, so a screen that shows nothing says where it looked.
    path: str
    exists: bool
    total: int = 0
    skipped: int = 0
    runs: list[LineupRunRow] = []
    error: str | None = None
    last_at: str | None = None
    last_age_hours: float | None = None
    #: The newest run is older than `STALE_AFTER_HOURS`. A job that stopped running writes no
    #: row at all, so without this a list of green rows from last week reads as fine.
    stale: bool = False


def read_lineup_runs(
    path: Path, *, now: datetime, limit: int = DEFAULT_RUNS_LIMIT
) -> LineupRuns:
    """The scheduled lineup's history at `path`, newest first, and how old the newest run is.

    Three answers a screen must tell apart, as the room journal's reader does: **missing** is
    "no scheduled run recorded yet"; **unreadable** is its own error, because rendering a
    directory-where-a-file-should-be as "nothing yet" sends the operator looking for a path
    that is already right; and **present**, with the age of its newest row. `now` is this
    endpoint's `_now()`, so the app's lineup surface still reads the clock in one place.
    """
    import dataclasses

    from fantabot.adapters.files.lineup_runs import read_runs

    limit = max(1, min(limit, MAX_RUNS_LIMIT))
    shown = str(path)
    existed = path.exists()
    try:
        runs, skipped = read_runs(path)
    except OSError as exc:  # a directory, a permission, a vanished volume
        return LineupRuns(ok=False, path=shown, exists=True, error=type(exc).__name__)
    if not existed:
        return LineupRuns(ok=True, path=shown, exists=False)

    newest = runs[0] if runs else None
    age: float | None = None
    if newest is not None:
        try:
            age = (now.astimezone() - datetime.fromisoformat(newest.at)).total_seconds() / 3600
        except (ValueError, TypeError):
            age = None
    return LineupRuns(
        ok=True,
        path=shown,
        exists=True,
        total=len(runs),
        skipped=skipped,
        runs=[LineupRunRow(**dataclasses.asdict(run)) for run in runs[:limit]],
        last_at=newest.at if newest else None,
        last_age_hours=None if age is None else round(age, 1),
        stale=age is not None and age > STALE_AFTER_HOURS,
    )


@router.get("/lineup/runs", response_model=LineupRuns, tags=["lineup"])
def lineup_runs(limit: int = DEFAULT_RUNS_LIMIT) -> LineupRuns:
    """What the scheduled lineup job did, read back. **The app writes nothing here** and
    offers no control over the job: turning it on and off lives in `.env` and the `launchd`
    plist, by the operator's choice, and `arming.py` exists so no arming decision is stored.
    """
    from fantabot.config import lineup_runs_path

    return read_lineup_runs(lineup_runs_path(), now=_now(), limit=limit)


# -- what the platform has saved right now ------------------------------------------------


class CurrentLineup(BaseModel):
    """`fantabot lineup show`, as a value.

    **Ids, not names.** The command prints ids and so does this: naming them would mean
    running `build_plans` — seven live reads and a solve — to annotate a read that has
    already answered, and it would fail for reasons that have nothing to do with the saved
    lineup. The page holds the plan for the same lega and joins the names client-side,
    falling back to the id, which costs nothing it was not already paying.
    """

    #: One of `api/outcomes.LINEUP_CURRENT_OUTCOMES`.
    outcome: str
    reason: str = ""
    module: str = ""
    starters: list[int] = []
    bench: list[int] = []


@router.get("/lineup/current", response_model=CurrentLineup, tags=["lineup"])
def lineup_current(league_id: int, competition: int) -> CurrentLineup:
    """The lineup the platform currently holds for one competition. **Read-only.**

    A different question from `GET /lineup/plan`, and the difference is the point: that one
    asks what we *should* field, this asks what is *saved*. On a matchday where a submit was
    refused — an unfieldable module, a deadline already past — the two answers differ, and
    that is exactly when an operator needs to see both.

    `competition` is required rather than resolved. A lineup belongs to a competition, and
    picking one would answer a question nobody asked; `lineup show` refuses without one for
    the same reason.
    """
    from fantabot.domain.tokens.errors import (
        ApiTimeout,
        ApiUnavailable,
        AppKeyRejected,
        TokenError,
        TokenRejected,
    )
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot_app.api.outcomes import because

    try:
        with _open_store() as store:
            body = teamLineup_read(league_id, competition, store=store)
    except (TokenRejected, AppKeyRejected) as exc:
        # Both, and before the `TokenError` clause, because `AppKeyRejected` **is** a
        # `TokenError`: caught only as one, the same platform rejection reads as
        # `no_credential` here and `refused` on `GET /lineup/plan` — two routes, one
        # credential, two different remedies on screen, which is the thing `outcomes.py`
        # exists to prevent.
        return CurrentLineup(outcome="refused", reason=str(exc))
    except (ApiTimeout, ApiUnavailable) as exc:
        return CurrentLineup(outcome="unreachable", reason=str(exc))
    except TokenError as exc:
        return CurrentLineup(outcome="no_credential", reason=str(exc))
    except (SQLAlchemyError, OSError) as exc:
        return CurrentLineup(outcome="unreachable", reason=because(exc))

    dto = body.get("teamLineupDto") or {}
    if not dto:
        return CurrentLineup(
            outcome="no_lineup",
            reason="no lineup has been saved for this competition yet.",
        )
    return CurrentLineup(
        outcome="read",
        module=str(dto.get("mdl", "")),
        starters=[int(p) for p in dto.get("starts", [])],
        bench=[int(p) for p in dto.get("bench", [])],
    )
