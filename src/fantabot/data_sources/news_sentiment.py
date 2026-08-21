"""Read the sentiment time-series back out.

The rule that governs everything here: **rows with ``confidenza == 0`` are
excluded from every average.** That value means "no coverage was found", not
"this player is neutral". Folding its 0.0 into a mean invents a data point and
destroys the distinction the schema exists to preserve — so a player whose only
rows are silent has no trailing average at all, and says so by returning ``None``.

This is the read side of ``fantabot news-fetch``. ``strategy.py`` does not consume
it yet; wiring ``disponibilita``/``rigorista`` into ``decide_bid`` and
``titolarita`` into ``pick_starting_lineup`` is a later phase.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

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
    player_id: str
    nome: str
    ruoli_mantra: str
    """The frozen late-July tag."""
    ruolo_campo: str
    """What recent coverage says he is actually being played as."""
    deriva_ruolo: float


class NewsSentimentSource:
    """Query the appended CSV. A missing file reads as empty, not as an error."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._by_player: dict[str, list[SentimentRow]] = defaultdict(list)
        self._load()

    def latest(self, player_id: str) -> SentimentRow | None:
        rows = self._by_player.get(player_id)
        return rows[-1] if rows else None

    def trailing(self, player_id: str, weeks: int = 4) -> TrailingSentiment | None:
        """Mean of each score over the last ``weeks`` runs, silent rows excluded."""
        window = [row for row in self._by_player.get(player_id, [])[-weeks:] if row.confidenza > 0]
        if not window:
            return None

        def mean(name: str) -> float:
            # float() around getattr: the attribute is typed float on SentimentRow,
            # but getattr erases that to Any and mypy --strict rejects returning it.
            return sum(float(getattr(row, name)) for row in window) / len(window)

        return TrailingSentiment(
            player_id=player_id,
            rows_used=len(window),
            sentiment=mean("sentiment"),
            disponibilita=mean("disponibilita"),
            titolarita=mean("titolarita"),
            mercato=mean("mercato"),
            forma=mean("forma"),
            rigorista=mean("rigorista"),
            piazzati=mean("piazzati"),
        )

    def drift(self, player_id: str) -> RoleDrift | None:
        """The latest role drift for one player, or ``None`` if the tag still holds."""
        row = self.latest(player_id)
        if row is None or row.deriva_ruolo <= 0:
            return None
        return RoleDrift(
            player_id=row.player_id,
            nome=row.nome,
            ruoli_mantra=row.ruoli_mantra,
            ruolo_campo=row.ruolo_campo,
            deriva_ruolo=row.deriva_ruolo,
        )

    def drifted(self) -> list[RoleDrift]:
        """Every player whose frozen Mantra tag no longer describes them, worst first.

        This is what a Mantra lineup engine actually wants: the platform will never
        correct these tags, so this list is the only warning that a schema slot is
        being filled by someone who no longer plays there.
        """
        found = [d for pid in self._by_player if (d := self.drift(pid)) is not None]
        return sorted(found, key=lambda d: (-d.deriva_ruolo, d.player_id))

    def _load(self) -> None:
        if not self._path.exists():
            return
        with self._path.open(newline="", encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                row = SentimentRow(
                    player_id=raw["id"],
                    nome=raw["nome"],
                    data_run=raw["data_run"],
                    ruolo_campo=raw["ruolo_campo"],
                    ruoli_mantra=raw["ruoli_mantra"],
                    deriva_ruolo=float(raw["deriva_ruolo"]),
                    **{name: float(raw[name]) for name in SCORES},
                )
                self._by_player[row.player_id].append(row)
        # Sorted by run date, so `latest` and the trailing window do not depend on
        # the order rows happen to sit in the file.
        for rows in self._by_player.values():
            rows.sort(key=lambda r: r.data_run)
