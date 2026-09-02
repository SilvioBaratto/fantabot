"""Writes for the lega snapshot tables (`adapters/persistence/models/league.py`).

**Append-only, never an upsert.** That module's own docstring states the reason: these
are point-in-time captures keyed on `captured_at`, and the point is the drift between
them. Overwriting one in place would be the one thing this table exists to refuse — a
plain `INSERT`, not `ON CONFLICT DO UPDATE`, is what the shape asks for.
"""

from __future__ import annotations

from fantabot.adapters.persistence.models.league import LeagueTeamSnapshot
from fantabot.adapters.persistence.repositories._base import RepositoryBase
from fantabot.domain.shared.league import TeamSnapshot


class LeagueRepository(RepositoryBase):
    """Writes for `league_snapshot`, `league_team_snapshot`, `league_player_pool`."""

    def record_team_snapshot(self, snapshot: TeamSnapshot) -> None:
        """Insert one new `league_team_snapshot` row. `captured_at` is the table's own
        `now()` default — every call is a new capture, never a correction of the last."""
        self.session.add(
            LeagueTeamSnapshot(
                league_id=snapshot.league_id,
                team_id=snapshot.team_id,
                user_id=snapshot.user_id,
                nome=snapshot.nome,
                owner=snapshot.owner,
                credits_initial=snapshot.credits_initial,
                credits_spent=snapshot.credits_spent,
                credits_remaining=snapshot.credits_remaining,
            )
        )
