"""Resolve which competition the lineup acts on. Pure.

A cron run passes no flag, so the competition is picked from `/league/competitions`: the one
not deleted that includes our team. Exactly one is the answer; zero or several is refused
(`NoCompetition` / `CompetitionAmbiguous`) rather than guessed, and the operator passes
`--competition`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fantabot.domain.lineup.errors import CompetitionAmbiguous, NoCompetition


def resolve_competition(
    competitions: Sequence[Mapping[str, object]],
    *,
    tid: int,
) -> int:
    """The id of the one active competition containing team `tid`.

    Raises `NoCompetition` if none matches, `CompetitionAmbiguous` if more than one does.
    """
    candidates: list[int] = [
        int(c["id"])  # type: ignore[call-overload]
        for c in competitions
        if not c.get("del") and tid in (c.get("tmids") or ())  # type: ignore[operator]
    ]
    if not candidates:
        raise NoCompetition(tid)
    if len(candidates) > 1:
        raise CompetitionAmbiguous(tuple(candidates))
    return candidates[0]
