"""News sentiment — the latest per-player reading and the role-drift list.

Reads fantabot's stored sentiment (written by `news fetch`). Pure builders map the
domain rows to response models; the endpoints degrade open (empty on DB error). The
feed is season-scoped, not per-league.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fantabot.domain.shared.values import RoleDrift, SentimentRow
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class NewsRow(BaseModel):
    player_id: str
    nome: str
    sentiment: float
    disponibilita: float
    titolarita: float
    forma: float
    confidenza: float
    ruoli_mantra: str


class DriftRow(BaseModel):
    player_id: str
    nome: str
    ruoli_mantra: str
    ruolo_campo: str
    deriva_ruolo: float


def build_news(latest: Mapping[str, SentimentRow], *, limit: int = 0) -> list[NewsRow]:
    """Map the latest-per-player readings to a feed, most positive sentiment first."""
    rows = [
        NewsRow(
            player_id=row.player_id,
            nome=row.nome,
            sentiment=row.sentiment,
            disponibilita=row.disponibilita,
            titolarita=row.titolarita,
            forma=row.forma,
            confidenza=row.confidenza,
            ruoli_mantra=row.ruoli_mantra,
        )
        for row in latest.values()
    ]
    rows.sort(key=lambda r: r.sentiment, reverse=True)
    return rows[:limit] if limit > 0 else rows


def build_drift(drifts: Sequence[RoleDrift]) -> list[DriftRow]:
    return [
        DriftRow(
            player_id=drift.player_id,
            nome=drift.nome,
            ruoli_mantra=drift.ruoli_mantra,
            ruolo_campo=drift.ruolo_campo,
            deriva_ruolo=drift.deriva_ruolo,
        )
        for drift in drifts
    ]


@router.get("/news", response_model=list[NewsRow], tags=["news"])
def news(limit: int = 0) -> list[NewsRow]:
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.sentiment import SentimentReadRepository

    try:
        with database_manager.get_session() as session:
            latest = SentimentReadRepository(session).all_latest()
        return build_news(latest, limit=limit)
    except Exception:  # noqa: BLE001 — degrade open: no data / no DB -> empty feed
        return []


@router.get("/news/drifted", response_model=list[DriftRow], tags=["news"])
def news_drifted() -> list[DriftRow]:
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.sentiment import SentimentReadRepository

    try:
        with database_manager.get_session() as session:
            drifts = SentimentReadRepository(session).drifted()
        return build_drift(drifts)
    except Exception:  # noqa: BLE001
        return []
