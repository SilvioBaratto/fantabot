"""Opponent reconstruction and the advisory surface. Pure.

Every sale names its buyer, so from the event stream we rebuild each rival's roster, spend
and role concentration live — "team 5 holds 3 Pc and 210 credits left" is arithmetic. The
render turns that, plus our target roster and per-target walk-aways, into the on-screen frame.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from fantabot.asta_engine.live import AssignmentEvent
from fantabot.asta_engine.state import OptimizationResult


@dataclass(frozen=True)
class OpponentState:
    """A rival as reconstructed from the feed."""

    team_id: str
    players: tuple[str, ...]
    spent: int
    role_counts: Mapping[str, int]

    def remaining(self, total_budget: int) -> int:
        return total_budget - self.spent


def track_opponents(
    events: Iterable[AssignmentEvent],
    *,
    our_team_id: str,
    roles_by_id: Mapping[str, Sequence[str]],
) -> dict[str, OpponentState]:
    """Rebuild every rival's state from the sale feed. Our team and unnamed buyers are skipped."""
    players: dict[str, list[str]] = {}
    spent: dict[str, int] = {}
    counts: dict[str, dict[str, int]] = {}
    for event in events:
        team = event.buyer_team_id
        if team is None or team == our_team_id:
            continue
        players.setdefault(team, []).append(event.player_id)
        spent[team] = spent.get(team, 0) + event.price
        role_count = counts.setdefault(team, {})
        for role in roles_by_id.get(event.player_id, ()):
            role_count[role] = role_count.get(role, 0) + 1
    return {
        team: OpponentState(team, tuple(players[team]), spent[team], counts[team])
        for team in players
    }


def format_opponents(
    opponents: Mapping[str, OpponentState], names: Mapping[str, str], total_budget: int
) -> str:
    """One line per rival: name, players held, spent and remaining credits."""
    lines = ["opponents:"]
    for team_id, opponent in sorted(opponents.items(), key=lambda kv: kv[1].spent, reverse=True):
        lines.append(
            f"  {names.get(team_id, team_id):<20} {len(opponent.players):>2} players | "
            f"spent {opponent.spent:>3} | left {opponent.remaining(total_budget):>3}"
        )
    return "\n".join(lines)


def format_advisory(
    result: OptimizationResult, walkaways: Mapping[str, float], names: Mapping[str, str]
) -> str:
    """Our target roster and the walk-away for each target, highest first."""
    lines = [
        f"target roster: {len(result.optimal)} players | obj {result.optimal.objective:.1f}"
    ]
    for player_id, walkaway in sorted(walkaways.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"  chase {names.get(player_id, player_id):<20} walk-away {walkaway:.0f}")
    return "\n".join(lines)
