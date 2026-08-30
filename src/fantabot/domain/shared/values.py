"""Value types shared across the read side. Pure: no I/O, no SQLAlchemy.

They live here rather than beside either consumer because both the repository
that produces them and the source that serves them need them, and importing
either from the other would be a cycle.

``QuotazioneRow`` arrived for the second reason. It was defined in
``db/repositories/reference.py`` and named in the signatures of ``news.build_pool``
and ``asta_engine.build_plan_inputs`` — both pure functions, both consequently
importing the repository module to spell their own arguments. A ``TYPE_CHECKING``
guard hid that from the interpreter but not from the design: a function whose
parameters are written in terms of a repository belongs to the repository's layer.

``SCORES`` is the single definition of the eight model-produced scores. It had
three copies before this module existed — ``news/store.py::_SCORES``,
``news_sentiment.py::SCORES`` and ``db/models/sentiment.py::SCORE_COLUMNS`` —
which is three places for the order to disagree.

**There is no stats-source interface here, and that is deliberate.** A ``StatsSource``
Protocol — ``projected_scores`` / ``player_pool`` / ``target_price`` — used to sit beside
these types, declared against a per-matchday provider that was never chosen. It went with
the Classic lineup scaffolding it was written for (``lineup.py``, ``auction.py``,
``strategy.py``): an interface with no implementation and no caller is a guess about a
shape, and this one had been guessed three phases before anything would consume it. The
asta engine does not need it — it prices from ``quotazioni.fvm``, the observed clearing
prices in ``asta_assignment`` and the sentiment feed, none of which that Protocol
described. When a per-matchday source is picked, the interface gets written against the
consumer that exists at the time.
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
class QuotazioneRow:
    """One valuation, joined to the player's name."""

    player_id: str
    nome: str
    squadra: str
    ruoli_codice: tuple[str, ...]
    ruoli: tuple[str, ...]
    #: Fantavalore di mercato — the market's value estimate. Defaulted so older callers
    #: that build this row without it keep working.
    fvm: int = 0


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
