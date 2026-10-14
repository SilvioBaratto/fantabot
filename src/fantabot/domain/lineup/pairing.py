"""A roster player's upcoming Serie A match, read off the lineup DTO's own fields. Pure and total.

`teamLineup_read` already returns, per roster player, the match his club plays in the round the
lineup is for: `teamH` and `teamA` (three-letter codes, the vocabulary of `quotazioni.squadra`),
`hoaw` (0 when his club is `teamH`), `tid`/`tidOp` (the platform's own ids for his club and the
opponent) and `tname` (his club's full name). Nothing in those fields was read until the
opponent adjustment (`matchup.py`) needed them.

**An unresolvable row is absent, never guessed at.** Each field is a different statement of the
same fact, and the row is used only when they agree: the club named by `tname` must be exactly
one end of `teamH`-`teamA`, and `hoaw` must say which. The own club comes from the row and not
from `quotazioni`, because `teamH`/`teamA` are relative to the platform's current club for the
player and a transfer is invisible to a table keyed on the preseason one.

**This function never raises.** The planner calls it for every roster row on the default path,
whatever the setting says, so a row it cannot read must cost one player's opponent and never the
hourly lineup.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fantabot.domain.shared.club_names import code_for

_HOME, _AWAY = 0, 1


@dataclass(frozen=True, slots=True)
class Pairing:
    """The two clubs of a player's upcoming match."""

    #: The player's own club, as the platform has it today.
    own: str
    #: The club he faces.
    opponent: str


def pairing_from_row(row: Mapping[str, Any]) -> Pairing | None:
    """The player's upcoming match, or `None` when the row does not state one consistently.

    Args:
        row: One `lineUpInfo` entry of a `teamLineup_read` response.

    Returns:
        The own club and the opponent as three-letter codes, or `None` when a code is blank or
        malformed, `tidOp` is missing or zero, the own club is neither `teamH` nor `teamA`,
        the match is a club against itself, or `hoaw` is absent or disagrees with the rest.
    """
    home, away = _code(row.get("teamH")), _code(row.get("teamA"))
    name = row.get("tname")
    if home is None or away is None or home == away or not isinstance(name, str):
        return None
    own = code_for(name)
    if own == home:
        opponent, expected = away, _HOME
    elif own == away:
        opponent, expected = home, _AWAY
    else:
        return None
    hoaw = row.get("hoaw")
    if isinstance(hoaw, bool) or not isinstance(hoaw, int) or hoaw != expected:
        return None
    opponent_id = row.get("tidOp")
    if isinstance(opponent_id, bool) or not isinstance(opponent_id, int) or opponent_id == 0:
        return None
    return Pairing(own=own, opponent=opponent)


def _code(value: object) -> str | None:
    """A three-letter club code, upper-cased, or `None` for anything else."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if len(code) == 3 and code.isascii() and code.isalpha() else None
