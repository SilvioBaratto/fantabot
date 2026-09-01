"""A pasted link and a stored uid become a room we can bid in.

The fetch is injected. This layer never opens a socket, never sees the Bearer, and never
touches Postgres — the interface binds `rest.fetch_league` with the token and hands the body
in. `test_the_module_is_structurally_unable_to` proves the last of those with the import graph
rather than trusting the docstring, which is why `PlanInputs` had to move to a leaf module
first: anything defined beside `read_plan_inputs` carries a path to persistence with it.

⚠ **`rest.fetch_league` has never run against a real room.** It had no caller in `src/` at all
before this phase — only tests. The shapes here are what `docs/fantalab/06 §3` records as
observed; the first live `--resolve-only` is what confirms them.

**Three rooms are refused rather than entered**, each because bidding in one would be wrong in
a way the platform will not tell us about:

* not Mantra — `domain/asta` is Mantra only, and a Classic room has no schema matrix to check
  a rosa against;
* `raise_mode: ordered` — the `raise_state` array such a room expects is undecoded
  (`docs/fantalab/06 §8`), so our payload would be malformed, and a malformed raise returns
  the same `401` as a lost race;
* unseated — bidding is unauthenticated and the server validates no identity, so a wrong seat
  is accepted with a `200` and drives somebody else's team all evening.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fantabot.adapters.http.fantalab.rest import RoomConfig, Seat


class RoomRefused(RuntimeError):
    """This room cannot be bid in, and the message says what the operator can do about it."""


@dataclass(frozen=True)
class ResolvedRoom:
    """Everything the live loop needs, and nothing that could leak a credential."""

    fantaleague_id: str
    db: int | None
    seat: Seat
    num_teams: int | None
    num_credits: int | None
    min_player: int | None
    max_player: int | None
    asta_mode: str | None
    raise_mode: str | None
    counter_time: int | None
    counter_time_first: int | None
    call_at_quotaz: bool
    #: `fantateam_id -> team name`, so a rival reads as a name rather than a uuid. Uuids are
    #: unreadable at speed, and the screen is read at speed or not at all.
    team_names: Mapping[str, str]

    @property
    def budget(self) -> float:
        """The room's credits, or FantaLab's default when it does not say."""
        return float(self.num_credits if self.num_credits is not None else 500)


def resolve_room(
    fantaleague_id: str,
    *,
    user_id: str,
    fetch: Callable[[str], RoomConfig],
) -> ResolvedRoom:
    """Fetch the room and check we can legitimately bid in it. Raises `RoomRefused` if not.

    Takes an already-parsed `RoomConfig`: `rest.fetch_league` does the parsing and has its own
    tests for it, and re-parsing here would give two places to disagree about a field.
    """
    config = fetch(fantaleague_id)

    if config.asta_type and config.asta_type != "mantra":
        raise RoomRefused(
            f"this room is {config.asta_type}, not mantra. Nothing here can field a Classic "
            "rosa — domain/asta is Mantra only, twelve role codes across eleven schemi."
        )

    if config.raise_mode and config.raise_mode != "free":
        raise RoomRefused(
            f"raise_mode is {config.raise_mode!r}, not 'free'. The raise_state array an "
            "ordered room expects is undecoded (docs/fantalab/06 §8), so our payload would be "
            "malformed — and a malformed raise returns the same 401 as a lost race, so we "
            "would not even be able to tell. Bid this room by hand."
        )

    seat = config.seat_of(user_id)
    if seat is None:
        free = ", ".join(s.team_name or s.fantateam_id for s in config.free_seats())
        raise RoomRefused(
            "we hold no seat in this room. Claim one in the browser first — bidding is "
            "unauthenticated and the server validates no identity, so driving a seat we do "
            f"not own would be accepted with a 200. Free seats: {free or '(none)'}."
        )

    return ResolvedRoom(
        fantaleague_id=config.fantaleague_id,
        db=config.db,
        seat=seat,
        num_teams=config.num_teams,
        num_credits=config.num_credits,
        min_player=config.min_player,
        max_player=config.max_player,
        asta_mode=config.asta_mode,
        raise_mode=config.raise_mode,
        counter_time=config.counter_time,
        counter_time_first=config.counter_time_first,
        call_at_quotaz=config.call_at_quotaz,
        team_names={s.fantateam_id: s.team_name or s.fantateam_id for s in config.seats},
    )
