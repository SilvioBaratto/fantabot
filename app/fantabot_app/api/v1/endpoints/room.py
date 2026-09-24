"""Room check — paste a room link, read back what the room says about itself.

The same path `fantabot asta live --resolve-only` walks, and entirely read-only:
`parse_room_url` -> `resolve_room` -> `rules_for_room`. `app/CLAUDE.md`'s guard bans the
*acting* names and deliberately permits `resolve_room`, which is what makes this
buildable at all — reading a room's configuration is not the same act as bidding in it.

**It is also the only call that proves a stored FantaLab session still authenticates.**
The credential is captured by the Accounts page and read by two other things. One reads it
*through here*: `POST /asta/room/bid` calls `check_room` before it starts the bidder
(`endpoints/room_bid.py`), so the only route that can spend credits asks this question
first. The other does not: `endpoints/actions.py` builds its own `FantalabStore` for
`POST /actions/harvest-scan`, where `LiveAuctionsClient.from_store` resolves the bearer in
the adapter and raises `AuthExpired` when the stored session has stopped authenticating.
So this is the route that asks the question *of the room*, not the only place an expired
session shows up.

**This one does not degrade open.** T31 (§3.3 of the app's maintainer-local `BACKLOG.md`)
records the cost of `except Exception -> found=False`: a database outage, a missing
season, an infeasible roster and a wrong `--format` all rendered as "No plan yet".
Degrade-open is right for a status read and wrong for the one call that tells the operator
whether they can bid tonight, so each outcome below carries its own reason and remedy.

**No bearer enters this module.** `rest.fetcher_from(store)` resolves it inside the
adapter and keeps it in a closure; the `user_id` comes from
`FantalabSessionRepository.describe()`, which returns no ciphertext and needs no cipher.
Both order by `captured_at DESC`, so the uid read here is the uid whose bearer the
fetcher will use.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from fantabot_app.api.infrastructure import processes
from fantabot_app.api.infrastructure.jobs import registry
from fantabot_app.api.outcomes import because
from fantabot_app.api.v1.endpoints.jobs import JobStarted

router = APIRouter()

#: Five answers, five names. Pinned as a tuple because §3.4's defect was four different
#: failures wearing one label, and the fix is not a better message — it is that the
#: outcomes stop being the same value.
#:
#: `no_credential` rather than `no_session`: nothing stored and stored-under-another-key
#: are both "the credential is not usable", and both carry a remedy the operator has to
#: perform by hand. Found by the first live probe, which met a `TokenUndecryptable` — the
#: FantaLab row in the bundled database was written under key `aa695c77` while `.env`
#: now holds `ef341176` (the two-databases migration excluded credentials on purpose). Calling that "no session" would have sent the operator to reconnect without
#: telling them their key had changed under it.
OUTCOMES = ("resolved", "refused", "bad_link", "no_credential", "unreachable")


class RoomCheck(BaseModel):
    outcome: str
    #: Always populated except on `resolved`, where the fields below are the answer.
    reason: str = ""
    fantaleague_id: str | None = None
    shard: int | None = None
    asta_type: str | None = None
    asta_mode: str | None = None
    raise_mode: str | None = None
    num_teams: int | None = None
    num_credits: int | None = None
    seat_team_id: str | None = None
    seat_team_name: str | None = None
    #: **Ours**, from the stored FantaLab session — the uid a bid payload is signed with, and
    #: the one `resolve_room` matched our chair by. Carried because `POST /asta/room/bid`
    #: needs it and resolving the room a second time to learn it would be a second resolution
    #: path with a second set of outcomes. It is not a credential: the bearer is resolved
    #: inside `rest.fetcher_from` and never enters this module, which is what the header of
    #: this file promises.
    seat_user_id: str | None = None
    roster_size: int | None = None
    #: `read from the room` / `assumed — nothing was declared`. Carried beside the
    #: size because a band nobody declared and a band the room stated are different facts,
    #: and only one of them is worth planning on.
    roster_provenance: str = ""


#: Opens the credential and returns `(user_id, fetch)`. A callable rather than two
#: arguments so that **nothing touches the store until a link has parsed** — the first
#: live probe answered `not a link` with a decryption failure, which is a true statement
#: about the credential and a useless answer to the question that was asked.
Connect = Callable[[], tuple[str, Callable[[str], Any]]]


def check_room(url: str, *, connect: Connect) -> RoomCheck:
    """Resolve a pasted room link. Pure of I/O — `connect` is the only door out.

    `connect` hands back `rest.fetcher_from(store)`, bound while a database session was
    open, so this frame never holds a credential and the tests never need one.
    """
    from fantabot.application.asta_room import RoomRefused, resolve_room
    from fantabot.domain.asta.live import parse_room_url
    from fantabot.domain.asta.state import rules_for_room
    from fantabot.domain.tokens.errors import TokenError

    try:
        fantaleague_id = parse_room_url(url)
    except ValueError as exc:
        # `InvitationLink` is a ValueError and is caught here on purpose: its own message
        # already says what to paste instead, and that is exactly what an admin sends.
        return RoomCheck(outcome="bad_link", reason=str(exc))

    try:
        user_id, fetch = connect()
    except TokenError as exc:
        # Nothing stored, or stored under a key this process does not hold. Each says so
        # in its own words, and each names a different thing for the operator to do.
        return RoomCheck(
            outcome="no_credential", reason=str(exc), fantaleague_id=fantaleague_id
        )
    except (SQLAlchemyError, OSError) as exc:
        # A database that will not open is its own answer. Named rather than caught bare:
        # `stored_connect` opens a session, reads one row and builds a cipher, so the only
        # non-`TokenError` ways it can fail are the driver's and the socket's. Anything
        # else here is a bug in this repository and reaches FastAPI as a 500 — which is
        # `api/outcomes.py`'s rule, and this file is the one that module calls the model.
        # It held two bare handlers until 2026-09-24 and was the sole thing the widened
        # ban turned red.
        return RoomCheck(
            outcome="unreachable", reason=because(exc), fantaleague_id=fantaleague_id
        )

    try:
        resolved = resolve_room(fantaleague_id, user_id=user_id, fetch=fetch)
    except RoomRefused as exc:
        # The room answered, and what it answered is no. Carrying the id says the link
        # was fine — only the room is not one we can drive.
        return RoomCheck(outcome="refused", reason=str(exc), fantaleague_id=fantaleague_id)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        # A fetch that failed is not a refusal: the room said nothing, we could not ask it.
        #
        # ⚠ **`httpx.HTTPError` is named because `rest` is not `apileague`.**
        # `apileague._send` maps every transport failure onto `ApiTimeout`/`ApiUnavailable`,
        # so a route reading *that* host never meets a raw httpx error. `rest.fetch_league`
        # maps nothing, and `httpx.HTTPError` inherits from `Exception` directly rather
        # than from `OSError` — so a clause naming only the socket would let it past.
        # `raise_for_status()` is how a room answering `401` arrives here, and `ValueError`
        # covers the malformed body (`json.JSONDecodeError` is one) that `parse_league` is
        # handed. The same three families as `endpoints/asta.py`'s advisory, which reads
        # this same host through `rtdb` for the same reason.
        return RoomCheck(
            outcome="unreachable", reason=because(exc), fantaleague_id=fantaleague_id
        )

    rules, provenance = rules_for_room(
        selection=resolved.number_of_players_selection,
        min_goalkeepers=resolved.min_goalkeepers,
        min_others=resolved.min_others,
        classic_band=resolved.players_settings_data,
    )
    return RoomCheck(
        outcome="resolved",
        seat_user_id=user_id,
        fantaleague_id=resolved.fantaleague_id,
        shard=resolved.db,
        asta_type=resolved.asta_type,
        asta_mode=resolved.asta_mode,
        raise_mode=resolved.raise_mode,
        num_teams=resolved.num_teams,
        num_credits=resolved.num_credits,
        seat_team_id=resolved.seat.fantateam_id,
        seat_team_name=resolved.seat.team_name,
        roster_size=rules.size,
        roster_provenance=provenance,
    )


def stored_connect() -> tuple[str, Callable[[str], Any]]:
    """The real `Connect`: the newest stored session's uid, and a fetcher bound to it.

    `describe()` returns no ciphertext and needs no cipher, and both it and
    `FantalabStore.load` order by `captured_at DESC` — so the uid read here is the uid
    whose bearer the fetcher will resolve. `fetcher_from` keeps that bearer in a closure
    inside the adapter; it never reaches this frame.
    """
    from fantabot.adapters.http.fantalab import rest
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.tokens import FantalabSessionRepository
    from fantabot.adapters.tokens.fantalab_store import FantalabStore
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import FantalabSessionMissing

    with database_manager.get_session() as session:
        stored = FantalabSessionRepository(session).describe()
        if not stored:
            raise FantalabSessionMissing()
        store = FantalabStore(session, TokenCipher(settings.fantabot_encryption_key))
        # Bound while the session is open. `fetcher_from` resolves the bearer now, so a
        # key that cannot decrypt this row raises here rather than at the first request.
        return stored[0][0], rest.fetcher_from(store)


@router.get("/asta/room", response_model=RoomCheck, tags=["asta"])
def room_check(url: str) -> RoomCheck:
    return check_room(url, connect=stored_connect)


# -- watching one, supervised ---------------------------------------------------------


class WatchRequest(BaseModel):
    """A room link, or its fantaleague id. Nothing else, and that is the design.

    No `arm`, and no number from the value model. The first is the lock the operator
    opens deliberately, for one room, at the keyboard — 3.9b is where the app learns to
    send it, behind `application/arming`'s contract. The second is the child's own option
    set: `--lam`, `--budget` and the three alphas are declared once, in
    `interface/asta.py`, and a copy here is the second value model that
    `application/asta_planner.py` exists to prevent — three commands once held three, and
    `asta bid` planned on plain `fvm` for a week after `asta optimize` had stopped.
    """

    url: str


def watch_flag(fantaleague_id: str) -> Path:
    """`room-<id>.watch.stop`, beside the journal. One flag per (room, role).

    A watch takes no landing-zone role, so `ProcessJob` needs a flag of its own, and
    `stop_path` refuses any role but `collector` and `loader` — rightly: its two are a
    contract about who may hold a landing zone. `room_stop_path` is the room's equivalent
    and carries the same two reasons: per-room, so a stop aimed at one does not reach the
    other, and per-role, so Stop on a watch does not end a bid on the same room.

    Beside the journal because that is the artefact the run is about, the same way a
    harvest flag sits beside its landing zone. `journal_path()` is resolved, so the flag
    does not move when the working directory does.
    """
    from fantabot.adapters.files.stopflag import room_stop_path
    from fantabot.config import journal_path

    # The derivation is `fantabot`'s since 3.9b, and the child calls the same function with
    # the same `journal_path()` — one spelling of one fact, rather than a shape the app
    # restates and the CLI has to agree with by inspection.
    #
    # Annotated, not returned bare: `fantabot` ships no `py.typed`, so this venv's mypy
    # reads every symbol from it as `Any` — `processes._request_stop`'s reason for the
    # same shape.
    flag: Path = room_stop_path(journal_path(), fantaleague_id, "watch")
    return flag


@router.post("/asta/room/watch", response_model=JobStarted, tags=["asta"])
def room_watch(request: WatchRequest) -> JobStarted:
    """Watch a live room, supervised as a child process. It reads; it never bids.

    **A subprocess, not a thread**, and that is what makes the accept criterion true:
    closing the tab does not stop the watch, because the run was never the request's to
    own. A reopened tab finds it again through `GET /jobs`, which is also what stops it
    starting a second one.

    **The journal is the channel, not stdout.** `asta room` paints a Rich `Live`, and a
    `Live` on a pipe renders to nobody — so the child's own screen stays in the terminal
    and the app reads `GET /asta/journal?follow=1`, the file both live commands already
    append a row to per cycle. That is what 3.6's lift bought: one function produces the
    row, whichever surface is driving.

    **The copilot is off.** Its pane is drawn into that same unread screen, and every
    brief is a real model call — cost with no reader. The CLI keeps it on by default
    because there someone is looking.

    The link is parsed here rather than left to the child, for `harvest collect`'s reason:
    a refusal at the moment of the click is a thing an operator reads, and "started, then
    died two seconds later" is not. Everything the child alone can refuse — a missing
    FantaLab session, a room that says no — stays the child's and lands in the job log by
    name.

    **Stopping.** On POSIX the first stop is a `SIGINT`, and a run that was never armed
    has nothing to disarm, so it exits. On Windows no signal is sent and `asta room` does
    not poll the flag, so the first stop is silent and the second kills after the grace —
    ungraceful and, for a watch, harmless: the journal flushes per line. Teaching the CLI
    to poll the flag is 3.9b's, where the child being stopped is one that spends credits.
    """
    from fantabot.domain.asta.live import parse_room_url

    try:
        fantaleague_id = parse_room_url(request.url)
    except ValueError as exc:
        # `InvitationLink` is a ValueError and is caught by the same clause on purpose:
        # its message already names what to paste instead, which is exactly what an admin
        # sends. Same reading as `check_room`'s.
        raise HTTPException(status_code=400, detail=str(exc)) from None

    job = processes.ProcessJob(
        # The parsed id, not the pasted string: one spelling reaches the child, the flag
        # and the job log, and a query string never becomes an argv token.
        processes.fantabot_command("asta", "room", fantaleague_id, "--no-copilot"),
        flag=watch_flag(fantaleague_id),
    )
    return JobStarted(job_id=registry.start(job.run, kind="asta-watch", stop=job.stop))
