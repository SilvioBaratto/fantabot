"""The CSV store: one row per player per run, appended forever.

Append-only, deliberately. A past Wednesday cannot be regenerated — the news has
moved on — so rewriting the file is a data-loss event with no undo. The resume
index keyed on ``(data_run, id)`` is what makes killing a half-finished run free:
restart, and only the players without a row for today get queried again.

Floats are written with a ``.`` decimal separator. The scraped CSVs in ``data/``
use Italian comma-decimals, which ``data/README.md`` documents as a gotcha to
work around — not a convention worth propagating into a new file.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from .mantra import drift, parse_codes
from .models import PlayerSentiment
from .pool import PoolPlayer

COLUMNS: tuple[str, ...] = (
    "data_run",
    "giorni_lookback",
    "stagione",
    "id",
    "nome",
    "squadra",
    "ruolo",
    "ruoli_mantra",
    "ruolo_campo",
    "deriva_ruolo",
    "sentiment",
    "disponibilita",
    "titolarita",
    "mercato",
    "forma",
    "rigorista",
    "piazzati",
    "confidenza",
    "riassunto",
    "n_fonti",
    "fonti",
    "modello",
)

_SCORES: tuple[str, ...] = (
    "sentiment",
    "disponibilita",
    "titolarita",
    "mercato",
    "forma",
    "rigorista",
    "piazzati",
    "confidenza",
)


def build_row(
    player: PoolPlayer,
    sentiment: PlayerSentiment,
    data_run: date,
    giorni_lookback: int,
    stagione: str,
    modello: str,
) -> dict[str, str]:
    """Flatten one validated record into the CSV's string columns.

    ``deriva_ruolo`` is computed here rather than asked of the model: it compares
    what the model observed against the tag we hold, and the model does not know
    what tag we hold. See :mod:`fantabot.news.mantra`.
    """
    row = {
        "data_run": data_run.isoformat(),
        "giorni_lookback": str(giorni_lookback),
        "stagione": stagione,
        "id": player.id,
        "nome": player.nome,
        "squadra": player.squadra,
        "ruolo": player.ruolo,
        "ruoli_mantra": player.ruoli_mantra,
        # Normalized and sorted: live runs return the rules-doc casing the prompt's
        # legend uses ("B;Ds;E"), while ruoli_mantra beside it is uppercase. Drift was
        # already computed on parsed sets, but the stored cell has to be comparable
        # to its neighbour and greppable across the file.
        "ruolo_campo": ";".join(sorted(parse_codes(";".join(sentiment.ruolo_campo)))),
        "deriva_ruolo": _score(
            drift(sentiment.ruolo_campo, player.ruoli_mantra, sentiment.confidenza)
        ),
        "riassunto": sentiment.riassunto,
        "n_fonti": str(len(sentiment.fonti)),
        "fonti": ";".join(sentiment.fonti),
        "modello": modello,
    }
    row.update({name: _score(getattr(sentiment, name)) for name in _SCORES})
    return row


def append_rows(path: Path, rows: Sequence[dict[str, str]]) -> None:
    """Append rows, writing the header only when creating the file."""
    if not rows:
        return

    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def existing_keys(path: Path) -> set[tuple[str, str]]:
    """``(data_run, id)`` pairs already on disk. Empty when the file is absent."""
    if not path.exists():
        return set()

    with path.open(newline="", encoding="utf-8") as handle:
        return {(row["data_run"], row["id"]) for row in csv.DictReader(handle)}


def _score(value: float) -> str:
    return f"{value:.2f}"
