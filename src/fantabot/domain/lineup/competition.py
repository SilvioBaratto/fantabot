"""Resolve which competition the lineup acts on. Pure.

A cron run passes no flag, so the competition is picked from `/league/competitions`: the one
not deleted that includes our team. Exactly one is the answer; zero or several is refused
(`NoCompetition` / `CompetitionAmbiguous`) rather than guessed, and the operator passes
`--competition`.

One tie-break is not a guess: a lega with a championship (`type` 1) and a cup (`type` 4, the
`Coppa`) lists our team in both, and the platform keeps one lineup for all of them
(`allComp: true` on the saved DTO; the cup's own `teamLineup` read answers 400, measured
2026-09-22). So when exactly one candidate is a championship, it is the one acted on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fantabot.domain.lineup.errors import CompetitionAmbiguous, NoCompetition

#: `type` of a championship (`Campionato`) in `/league/competitions`; a cup is 4.
CHAMPIONSHIP_TYPE = 1


def resolve_competition(
    competitions: Sequence[Mapping[str, object]],
    *,
    tid: int,
) -> int:
    """The id of the one active competition containing team `tid`.

    Several candidates resolve to the championship when exactly one of them is one.
    Raises `NoCompetition` if none matches, `CompetitionAmbiguous` if still more than one does.
    """
    candidates = [
        c
        for c in competitions
        if not c.get("del") and tid in (c.get("tmids") or ())  # type: ignore[operator]
    ]
    if not candidates:
        raise NoCompetition(tid)
    if len(candidates) > 1:
        championships = [c for c in candidates if c.get("type") == CHAMPIONSHIP_TYPE]
        if len(championships) != 1:
            raise CompetitionAmbiguous(tuple(int(str(c["id"])) for c in candidates))
        candidates = championships
    return int(str(candidates[0]["id"]))
