"""The lineup projection's reads over Postgres: `domain.lineup.history.LineupHistory`.

Read-only. Three decisions in the SQL, each with a wrong version that passes on a small
fixture:

* **Coach rows never come back.** They are 3,099 `match_grain` rows with no id, and
  `player_id IN (...)` never matches a NULL — one guard, the one that is observable, so
  there is no second `IS NOT NULL` for a mutant to delete unnoticed.
* **"Before" is `data <`, never `giornata <`.** A postponed match keeps its giornata;
  `domain.lineup.history` says why that leaks.
* **The lega's scores come from one capture.** `league_competition` is append-only — a
  row per competition per sync — so a join that does not pick a capture multiplies every
  score by the number of syncs (stats-12), and an `IN` over every capture still admits a
  competition deleted since an older one. The competitions read are the ones the lega's
  **latest** capture holds and does not mark deleted, and only calculated rounds count:
  an uncalculated fixture carries 0 or null, which would read as a real, terrible score.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date

from sqlalchemy import func, select

from fantabot.adapters.persistence.models.league import LeagueCompetition, LeagueFixture
from fantabot.adapters.persistence.models.matches import MatchGrain
from fantabot.adapters.persistence.models.reference import Quotazione
from fantabot.adapters.persistence.repositories._base import RepositoryBase
from fantabot.adapters.persistence.repositories.sentiment import SentimentReadRepository
from fantabot.domain.lineup.history import Fixture, HistoryAppearance, Valuation
from fantabot.domain.lineup.scoring import Appearance
from fantabot.domain.shared.values import SentimentRow

#: The listone this lega plays. Roles and `qi` differ between the two.
MANTRA = "mantra"


class LineupHistoryRepository(RepositoryBase):
    def appearances(
        self, player_ids: Collection[int], *, seasons: Collection[str], before: date
    ) -> list[HistoryAppearance]:
        """Every row of these players in these seasons dated strictly before `before`,
        oldest first. An empty id or season set issues no statement."""
        if not player_ids or not seasons:
            return []
        records = self.session.execute(
            select(MatchGrain)
            .where(
                MatchGrain.player_id.in_(list(player_ids)),
                MatchGrain.stagione.in_(list(seasons)),
                MatchGrain.data < before,
                # Structurally nullable; no player row lacks it today, and one that did
                # would have no vote to score.
                MatchGrain.voto_fc.is_not(None),
            )
            .order_by(MatchGrain.data, MatchGrain.player_id)
        ).scalars().all()
        return [_appearance(record) for record in records]

    def roles(self, season: str, *, listone: str = MANTRA) -> dict[int, tuple[str, ...]]:
        """Each player's role codes in `season`'s listone."""
        rows = self.session.execute(
            select(Quotazione.player_id, Quotazione.ruoli_codice).where(
                Quotazione.stagione == season, Quotazione.listone == listone
            )
        ).all()
        return {int(player_id): tuple(codes) for player_id, codes in rows}

    def valuations(self, season: str, *, listone: str = MANTRA) -> dict[int, Valuation]:
        """Each player's preseason `qi` and club in `season`. Kept apart from `roles`: a
        replay takes roles from the current listone and these from the replayed season."""
        rows = self.session.execute(
            select(Quotazione.player_id, Quotazione.qi, Quotazione.squadra).where(
                Quotazione.stagione == season, Quotazione.listone == listone
            )
        ).all()
        return {
            int(player_id): Valuation(qi=int(qi), squadra=squadra)
            for player_id, qi, squadra in rows
        }

    def fixtures(self, season: str) -> list[Fixture]:
        """`season`'s Serie A matches, once each, in date order."""
        rows = self.session.execute(
            select(
                MatchGrain.giornata,
                MatchGrain.data,
                MatchGrain.squadra_raw,
                MatchGrain.avversario_raw,
                MatchGrain.gol_squadra,
                MatchGrain.gol_avversario,
            )
            .where(MatchGrain.stagione == season)
            .distinct()
            .order_by(MatchGrain.data, MatchGrain.squadra_raw)
        ).all()
        return [
            Fixture(
                season=season, giornata=giornata, played_on=played_on, home=home, away=away,
                home_goals=home_goals, away_goals=away_goals,
            )
            for giornata, played_on, home, away, home_goals, away_goals in rows
        ]

    def calculated_scores(self, league_id: int) -> list[float]:
        """Every fantapunti total of the lega's current competition's calculated rounds —
        both sides of each match — from its latest capture only."""
        latest = (
            select(func.max(LeagueCompetition.captured_at))
            .where(LeagueCompetition.league_id == league_id)
            .scalar_subquery()
        )
        current = select(LeagueCompetition.competition_id).where(
            LeagueCompetition.league_id == league_id,
            LeagueCompetition.captured_at == latest,
            LeagueCompetition.deleted.is_(False),
        )
        rows = self.session.execute(
            select(LeagueFixture.points_home, LeagueFixture.points_away)
            .where(
                LeagueFixture.competition_id.in_(current),
                LeagueFixture.calculated.is_(True),
            )
            .order_by(LeagueFixture.matchday, LeagueFixture.team_home)
        ).all()
        return [float(points) for pair in rows for points in pair if points is not None]

    def latest_sentiment(self, player_ids: Collection[int]) -> dict[int, SentimentRow]:
        """Each of these players' newest news reading, silent ones included."""
        if not player_ids:
            return {}
        wanted = {int(player_id) for player_id in player_ids}
        latest = SentimentReadRepository(self.session).all_latest()
        return {int(key): row for key, row in latest.items() if int(key) in wanted}


def _appearance(record: MatchGrain) -> HistoryAppearance:
    return HistoryAppearance(
        player_id=int(record.player_id or 0),
        role=record.ruolo_codice,
        fixture=Fixture(
            season=record.stagione,
            giornata=record.giornata,
            played_on=record.data,
            home=record.squadra_raw,
            away=record.avversario_raw,
            home_goals=record.gol_squadra,
            away_goals=record.gol_avversario,
        ),
        scored=Appearance(
            voto_fc=float(record.voto_fc or 0),
            gol_segnati=record.gol_segnati,
            rigori_segnati=record.rigori_segnati,
            assist=record.assist,
            ammonizione=record.ammonizione,
            espulsione=record.espulsione,
            autoreti=record.autoreti,
            rigori_parati=record.rigori_parati,
            rigori_sbagliati=record.rigori_sbagliati,
            gol_subiti=record.gol_subiti,
            mvp=record.mvp,
        ),
        fantavoto_fc=None if record.fantavoto_fc is None else float(record.fantavoto_fc),
    )
