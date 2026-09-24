"""Is this lega one to field a lineup in right now? Pure.

`league_status.activ` does not answer it: it read `true` for all five of the operator's leghe on
2026-09-22, three of which have no competition at all. What does answer it is the competition
list, the Serie A matchday, and whether the lineup has matchday coordinates yet.

The states, in the order they are decided:

* `relogin`        — the token is expired or refused. Not inactive: `auth login` fixes it.
* `gone`           — the lega answers 4xx (deleted, or the team was removed).
* `no_competition` — no live competition includes our team.
* `finished`       — every one of our competitions ended before this Serie A matchday.
* `not_started`    — every one of them starts after it; picked up by itself once it does.
* `ambiguous`      — several live competitions and no single championship among them.
* `idle`           — live, but the lineup has no matchday coordinates: nothing open to field.
* `open`           — a matchday is open. The only state `submit-all` submits in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fantabot.domain.lineup.competition import resolve_competition
from fantabot.domain.lineup.errors import CompetitionAmbiguous

ACTIVE_STATES = frozenset({"idle", "open"})


@dataclass(frozen=True)
class Activity:
    """One lega's state, why, and the competition a lineup would be fielded in."""

    state: str
    reason: str
    competition: int | None = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES


def _day(value: object, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def competition_activity(
    competitions: Sequence[Mapping[str, object]], *, tid: int, serie_a_matchday: int
) -> Activity:
    """Decide from the competition list alone. `open`/`idle` are split later, by
    `lineup_activity`, because that needs the chosen competition's lineup read."""
    mine = [
        c
        for c in competitions
        if not c.get("del") and tid in (c.get("tmids") or ())  # type: ignore[operator]
    ]
    if not mine:
        return Activity("no_competition", "no live competition includes our team")
    if all(_day(c.get("eDay"), 99) < serie_a_matchday for c in mine):
        return Activity("finished", f"every competition ended before Serie A {serie_a_matchday}")
    if all(_day(c.get("sDay"), 0) > serie_a_matchday for c in mine):
        first = min(_day(c.get("sDay"), 0) for c in mine)
        return Activity("not_started", f"starts at Serie A matchday {first}")
    try:
        comp = resolve_competition(mine, tid=tid)
    except CompetitionAmbiguous as exc:
        return Activity("ambiguous", str(exc))
    return Activity("idle", "competition live", competition=comp)


def lineup_activity(pending: Activity, dto: Mapping[str, object]) -> Activity:
    """`open` when the lineup DTO carries matchday coordinates, `idle` otherwise."""
    mday, cmday = _day(dto.get("mday"), 0), _day(dto.get("cmday"), 0)
    if mday and cmday:
        return Activity(
            "open", f"matchday {mday} open (Serie A {cmday})", competition=pending.competition
        )
    return Activity("idle", "no matchday open right now", competition=pending.competition)
