"""Room check — paste a room link, read back what the room says about itself.

The same path `fantabot asta live --resolve-only` walks, and entirely read-only:
`parse_room_url` -> `resolve_room` -> `rules_for_room`. `app/CLAUDE.md`'s guard bans the
*acting* names and deliberately permits `resolve_room`, which is what makes this
buildable at all — reading a room's configuration is not the same act as bidding in it.

**It is also the only call that proves a stored FantaLab session still authenticates.**
Today the credential is captured by the Accounts page and read by nothing in the app, so
"is FantaLab connected?" has had no answer beyond "a row exists".

**This one does not degrade open.** `todo/TODO.md` §3.4 records the cost of
`except Exception -> found=False`: a database outage, a missing season, an infeasible
roster and a wrong `--format` all rendered as "No plan yet". Degrade-open is right for a
status read and wrong for the one call that tells the operator whether they can bid
tonight, so each outcome below carries its own reason and its own remedy.

**No bearer enters this module.** `rest.fetcher_from(store)` resolves it inside the
adapter and keeps it in a closure; the `user_id` comes from
`FantalabSessionRepository.describe()`, which returns no ciphertext and needs no cipher.
Both order by `captured_at DESC`, so the uid read here is the uid whose bearer the
fetcher will use.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

#: Five answers, five names. Pinned as a tuple because §3.4's defect was four different
#: failures wearing one label, and the fix is not a better message — it is that the
#: outcomes stop being the same value.
#:
#: `no_credential` rather than `no_session`: nothing stored and stored-under-another-key
#: are both "the credential is not usable", and both carry a remedy the operator has to
#: perform by hand. Found by the first live probe, which met a `TokenUndecryptable` — the
#: FantaLab row in the bundled database was written under key `aa695c77` while `.env`
#: now holds `ef341176` (`todo/TODO.md` §1.2 excluded credentials from that migration on
#: purpose). Calling that "no session" would have sent the operator to reconnect without
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
    roster_size: int | None = None
    #: `read from the room` / `assumed — the room declared nothing`. Carried beside the
    #: size because a band nobody declared and a band the room stated are different facts,
    #: and only one of them is worth planning on.
    roster_provenance: str = ""


def _because(exc: Exception) -> str:
    """One line, typed. The idiom `tests/conftest.py` uses for the same reason: a driver
    traceback says the call failed and not which of five things failed."""
    return f"{type(exc).__name__}: {str(exc).splitlines()[0]}"


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
    except Exception as exc:  # noqa: BLE001 — a database that will not open is its own answer
        return RoomCheck(
            outcome="unreachable", reason=_because(exc), fantaleague_id=fantaleague_id
        )

    try:
        resolved = resolve_room(fantaleague_id, user_id=user_id, fetch=fetch)
    except RoomRefused as exc:
        # The room answered, and what it answered is no. Carrying the id says the link
        # was fine — only the room is not one we can drive.
        return RoomCheck(outcome="refused", reason=str(exc), fantaleague_id=fantaleague_id)
    except Exception as exc:  # noqa: BLE001 — a fetch that failed is not a refusal
        return RoomCheck(
            outcome="unreachable", reason=_because(exc), fantaleague_id=fantaleague_id
        )

    rules, provenance = rules_for_room(
        selection=resolved.number_of_players_selection,
        min_player=resolved.min_player,
        max_player=resolved.max_player,
        min_goalkeepers=resolved.min_goalkeepers,
        min_others=resolved.min_others,
        classic_band=resolved.players_settings_data,
    )
    return RoomCheck(
        outcome="resolved",
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
