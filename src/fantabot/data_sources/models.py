"""Value types for the sentiment read side. Pure: no I/O, no SQLAlchemy.

They live here rather than beside either consumer because both the repository
that produces them and the source that serves them need them, and importing
either from the other would be a cycle.

``SCORES`` is the single definition of the eight model-produced scores. It had
three copies before this module existed — ``news/store.py::_SCORES``,
``news_sentiment.py::SCORES`` and ``db/models/sentiment.py::SCORE_COLUMNS`` —
which is three places for the order to disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

SCORES: tuple[str, ...] = (
    "sentiment",
    "disponibilita",
    "titolarita",
    "mercato",
    "forma",
    "rigorista",
    "piazzati",
    "confidenza",
)


@dataclass(frozen=True)
class SentimentRow:
    """One player's reading from one run. Frozen, like fantabot's other values."""

    player_id: str
    nome: str
    data_run: str
    sentiment: float
    disponibilita: float
    titolarita: float
    mercato: float
    forma: float
    rigorista: float
    piazzati: float
    confidenza: float
    ruolo_campo: str
    ruoli_mantra: str
    deriva_ruolo: float


@dataclass(frozen=True)
class TrailingSentiment:
    """Mean of each score over a window, silent rows excluded."""

    player_id: str
    rows_used: int
    sentiment: float
    disponibilita: float
    titolarita: float
    mercato: float
    forma: float
    rigorista: float
    piazzati: float


@dataclass(frozen=True)
class RoleDrift:
    """A player whose frozen Mantra tag no longer describes him."""

    player_id: str
    nome: str
    ruoli_mantra: str
    """The frozen late-July tag."""
    ruolo_campo: str
    """What recent coverage says he is actually being played as."""
    deriva_ruolo: float
