"""The history `domain/lineup/predict` reads: last season's fantamedia, past clubs, fixtures.

Read-only, over tables the scrapers already fill. Player ids are the platform's own on both
sides — `lineUpInfo.pid` joined `players.id` 8 of 8 when measured 2026-09-22 — so there is no
mapping to maintain.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from fantabot.adapters.persistence.models.matches import MatchGrain
from fantabot.adapters.persistence.models.reference import Quotazione, Statistica, Team
from fantabot.adapters.persistence.repositories._base import RepositoryBase

#: Fewer graded games than this and last season's average is noise, not a prior.
MIN_PRIOR_GAMES = 5


class LineupHistoryRepository(RepositoryBase):
    """Reads for the lineup predictor. Never writes."""

    def latest_season(self) -> str | None:
        """The newest `stagione` in `quotazioni` — the season being played."""
        return self.session.execute(select(func.max(Quotazione.stagione))).scalar_one_or_none()

    def prior_fantamedia(self, ids: Collection[int], stagione: str) -> dict[int, float]:
        """`{player_id: media_fantavoto}` for `stagione` (fantacalcio grades, Classic listone),
        for players with at least `MIN_PRIOR_GAMES` games."""
        rows = self.session.execute(
            select(Statistica.player_id, Statistica.media_fantavoto).where(
                Statistica.stagione == stagione,
                Statistica.fonte == "fantacalcio",
                Statistica.listone == "classic",
                Statistica.player_id.in_(list(ids)),
                Statistica.media_fantavoto.is_not(None),
                Statistica.partite_giocate >= MIN_PRIOR_GAMES,
            )
        ).all()
        return {int(pid): float(fm) for pid, fm in rows}

    def prior_media_voto(self, ids: Collection[int], stagione: str) -> dict[int, float]:
        """`{player_id: media_voto}` — the plain-vote twin of `prior_fantamedia`, the prior for
        the modificatore difesa's vote forecast. Same source, same games floor."""
        rows = self.session.execute(
            select(Statistica.player_id, Statistica.media_voto).where(
                Statistica.stagione == stagione,
                Statistica.fonte == "fantacalcio",
                Statistica.listone == "classic",
                Statistica.player_id.in_(list(ids)),
                Statistica.media_voto.is_not(None),
                Statistica.partite_giocate >= MIN_PRIOR_GAMES,
            )
        ).all()
        return {int(pid): float(v) for pid, v in rows}

    def past_clubs(self, ids: Collection[int], before: str) -> dict[int, frozenset[str]]:
        """`{player_id: club codes}` he was listed at in any season before `before`."""
        rows = self.session.execute(
            select(Quotazione.player_id, Quotazione.squadra)
            .where(
                Quotazione.stagione < before,
                Quotazione.listone == "classic",
                Quotazione.player_id.in_(list(ids)),
            )
            .distinct()
        ).all()
        out: dict[int, set[str]] = {}
        for pid, club in rows:
            out.setdefault(int(pid), set()).add(club)
        return {pid: frozenset(clubs) for pid, clubs in out.items()}

    def fixtures(self, stagione: str) -> list[tuple[str, str, int, int]]:
        """Every played fixture of `stagione` as `(home, away, home_goals, away_goals)` codes.

        `match_grain` is one row per player; a fixture is its distinct
        `(giornata, squadra_raw, avversario_raw)` — `squadra_raw` is the home side for every
        row of a match block (`models/matches.py`), which is what makes this reading correct.
        Full club names are bridged to codes through `teams`.
        """
        home_t = aliased(Team)
        away_t = aliased(Team)
        rows = self.session.execute(
            select(
                home_t.codice,
                away_t.codice,
                MatchGrain.gol_squadra,
                MatchGrain.gol_avversario,
            )
            .join(
                home_t,
                (home_t.stagione == MatchGrain.stagione)
                & (home_t.nome_completo == MatchGrain.squadra_raw),
            )
            .join(
                away_t,
                (away_t.stagione == MatchGrain.stagione)
                & (away_t.nome_completo == MatchGrain.avversario_raw),
            )
            .where(MatchGrain.stagione == stagione)
            .distinct(MatchGrain.giornata, MatchGrain.squadra_raw, MatchGrain.avversario_raw)
        ).all()
        return [(h, a, int(hg), int(ag)) for h, a, hg, ag in rows]
