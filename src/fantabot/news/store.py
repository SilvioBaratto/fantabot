"""Flattening one validated record into the columns the store holds.

The storage itself is Postgres now —
``db/repositories/sentiment.py::SentimentRepository`` owns writing and the
resume index, and ``(data_run, player_id)`` is a primary key rather than a set
rebuilt by re-reading the whole file. What stays here is ``build_row``, which is
pure and is still the one place ``deriva_ruolo`` is computed.

``COLUMNS`` stays too, and is not merely documentation: a test asserts the
``player_sentiment`` table matches it name for name, so the two cannot drift.

Scores are formatted with a ``.`` decimal separator. The scraped CSVs in
``data/`` use Italian comma-decimals, which ``data/README.md`` documents as a
gotcha to work around — not a convention worth propagating.
"""

from __future__ import annotations

from datetime import date

from fantabot.data_sources.models import SCORES
from fantabot.news.mantra import drift, parse_codes
from fantabot.news.models import PlayerSentiment
from fantabot.news.pool import PoolPlayer

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

_SCORES: tuple[str, ...] = SCORES


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


def _score(value: float) -> str:
    return f"{value:.2f}"
