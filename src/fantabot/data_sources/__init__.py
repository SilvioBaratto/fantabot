"""Stats/injuries/probable-lineup provider interface.

Site not chosen yet — user is picking one. Implement it as a class satisfying
this Protocol (e.g. data_sources/fantacalcio_it.py or data_sources/some_api.py),
then wire it into lineup.py / auction.py. Nothing else in the codebase should
need to change: strategy.py only consumes ScoredPlayer objects.
"""

from typing import Protocol

from fantabot.models import Player, ScoredPlayer


class StatsSource(Protocol):
    def projected_scores(self, matchday: int) -> dict[str, ScoredPlayer]:
        """player.id -> ScoredPlayer for the given matchday."""
        ...

    def player_pool(self) -> list[Player]:
        """All players known to the source (for auction valuation)."""
        ...

    def target_price(self, player: Player) -> int:
        """This source's suggested fair-value credits for an auction target."""
        ...
