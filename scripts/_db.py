"""Shared database reads for the analysis scripts.

Replaces three divergent copies of the same loaders. Before this module,
``parse_decimal`` was defined in ``target_price.py:145``,
``analyze_low_minutes_bias.py:53`` and ``join_qi_bias_performance.py:48``, and
``load_prior_stats`` in the same three files — same intent, drifting details.

**Every query has an explicit ORDER BY.** The CSV versions read a file, so row
order was whatever the file held and was stable by accident. Postgres has no
inherent order, and these scripts fit regressions over the rows they return: an
unordered scan makes a model's coefficients wobble between runs for no reason
anyone could see.

The no-data handling that used to be ``if raw not in ("", "0,0")`` is now
``media_fantavoto IS NOT NULL`` — the importers already collapsed both markers
to NULL, which is the distinction those string comparisons were protecting.

Prerequisites this module adds to every script that imports it:
``pip install -e .`` and a running database (``docker compose up -d``).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from fantabot.db import database_manager


@contextmanager
def session() -> Iterator[Session]:
    """A read session. Same engine and pooling the CLI uses."""
    with database_manager.get_session() as handle:
        yield handle


@dataclass(frozen=True)
class PriorStats:
    partite_giocate: int
    media_fantavoto: float


@dataclass(frozen=True)
class BiasRow:
    stagione: str
    id: str
    nome: str
    squadra: str
    role: str
    qi: int
    pct_delta: float


@dataclass(frozen=True)
class PlayerQuote:
    stagione: str
    id: str
    nome: str
    squadra: str
    role: str
    qi: int
    qa: int
    fvm: int


def load_prior_stats(
    handle: Session, listone: str = "classic"
) -> dict[tuple[str, str], PriorStats]:
    """``(id, stagione)`` -> prior season stats, averaged across the three fonte.

    Rows whose ``media_fantavoto`` is NULL are excluded from the mean rather
    than folded in as zero — the source wrote ``"0,0"`` there, meaning it had no
    figure, and averaging that in would drag every prior toward zero.
    """
    rows = handle.execute(
        text(
            "SELECT player_id, stagione, partite_giocate, media_fantavoto "
            "FROM statistiche WHERE listone = :listone "
            "ORDER BY stagione, player_id, fonte"
        ),
        {"listone": listone},
    ).all()

    grouped: dict[tuple[str, str], list[tuple[int, float | None]]] = defaultdict(list)
    for player_id, stagione, partite, fantavoto in rows:
        grouped[(str(player_id), stagione)].append(
            (partite, float(fantavoto) if fantavoto is not None else None)
        )

    out: dict[tuple[str, str], PriorStats] = {}
    for key, entries in grouped.items():
        fantavoti = [value for _, value in entries if value is not None]
        if not fantavoti:
            continue
        out[key] = PriorStats(
            partite_giocate=entries[0][0],
            media_fantavoto=statistics.mean(fantavoti),
        )
    return out


def load_bias_rows(
    handle: Session,
    listone: str = "classic",
    *,
    seasons: set[str] | None = None,
    min_qi: int | None = None,
) -> list[BiasRow]:
    """Quote-to-price drift rows, filtered in SQL rather than in Python."""
    clauses = ["listone = :listone"]
    params: dict[str, object] = {"listone": listone}
    if seasons:
        clauses.append("stagione = ANY(:seasons)")
        params["seasons"] = sorted(seasons)
    if min_qi is not None:
        clauses.append("qi >= :min_qi")
        params["min_qi"] = min_qi

    rows = handle.execute(
        text(
            "SELECT b.stagione, b.player_id, p.nome, b.squadra, b.ruoli_codice[1], "
            "b.qi, b.pct_delta FROM qi_bias b JOIN players p ON p.id = b.player_id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY b.stagione, b.squadra, p.nome, b.player_id"
        ),
        params,
    ).all()

    return [
        BiasRow(
            stagione=stagione,
            id=str(player_id),
            nome=nome,
            squadra=squadra,
            role=(role or "").lower(),
            qi=qi,
            pct_delta=float(pct_delta),
        )
        for stagione, player_id, nome, squadra, role, qi, pct_delta in rows
    ]


def load_quotes(
    handle: Session, listone: str = "classic", *, seasons: set[str] | None = None
) -> list[PlayerQuote]:
    """Valuations for one listone, optionally restricted to some seasons."""
    clauses = ["q.listone = :listone"]
    params: dict[str, object] = {"listone": listone}
    if seasons:
        clauses.append("q.stagione = ANY(:seasons)")
        params["seasons"] = sorted(seasons)

    rows = handle.execute(
        text(
            "SELECT q.stagione, q.player_id, p.nome, q.squadra, q.ruoli_codice[1], "
            "q.qi, q.qa, q.fvm FROM quotazioni q JOIN players p ON p.id = q.player_id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY q.stagione, q.squadra, p.nome, q.player_id"
        ),
        params,
    ).all()

    return [
        PlayerQuote(
            stagione=stagione,
            id=str(player_id),
            nome=nome,
            squadra=squadra,
            role=(role or "").lower(),
            qi=qi,
            qa=qa,
            fvm=fvm,
        )
        for stagione, player_id, nome, squadra, role, qi, qa, fvm in rows
    ]
