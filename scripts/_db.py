"""Shared database reads for the analysis scripts.

Replaces three divergent copies of the same loaders. Before this module,
``parse_decimal`` was defined in ``target_price.py:145``,
``analyze_low_minutes_bias.py:53`` and ``join_qi_bias_performance.py:48``, and
``load_prior_stats`` in the same three files — same intent, drifting details.

**Every query has an explicit ORDER BY.** These scripts used to read files, so
row order was whatever the file held and was stable by accident. Postgres has no
inherent order, and these scripts fit regressions over the rows they return: an
unordered scan makes a model's coefficients wobble between runs for no reason
anyone could see.

The no-data handling that used to be ``if raw not in ("", "0,0")`` is now
``media_fantavoto IS NOT NULL``. Both of the site's no-data spellings — an
empty cell and the literal ``"0,0"`` — are collapsed to NULL on the way in by
``fantabot.parsing.italian_decimal``, which is the distinction those string
comparisons were protecting.

Prerequisites this module adds to every script that imports it:
``pip install -e .`` and a running database (``docker compose up -d``).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from fantabot.db import database_manager
from fantabot.parsing import split_codes, split_flags


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
    qa: int
    delta: int
    pct_delta: float

    @property
    def log_ratio(self) -> float:
        """``log(qa / qi)`` — what target_price fits its role fades on."""
        return math.log(self.qa / self.qi)


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
        # Strictly greater, matching the scripts: --min-qi 2 means "qi > 2",
        # the floor-effect guard. `>=` silently widened every sample by ~5%.
        clauses.append("qi > :min_qi")
        params["min_qi"] = min_qi

    rows = handle.execute(
        text(
            "SELECT b.stagione, b.player_id, p.nome, b.squadra, b.ruoli_codice, "
            "b.qi, b.delta, b.pct_delta "
            "FROM qi_bias b JOIN players p ON p.id = b.player_id "
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
            role=_role_string(codes, listone),
            qi=qi,
            qa=qi + delta,
            delta=delta,
            pct_delta=float(pct_delta),
        )
        for stagione, player_id, nome, squadra, codes, qi, delta, pct_delta in rows
    ]


def _role_string(codes: list[str], listone: str) -> str:
    """Reproduce the historical role spelling from the normalised codes.

    The two listoni never agreed: Classic was written as a single lower-case
    letter, Mantra as a ``;``-joined upper-case set. Both are stored as an
    upper-case array, so the spelling is reconstructed here rather than in the
    table — the scripts group and print by this string, and changing it would
    change their output for no reason.
    """
    joined = ";".join(codes)
    return joined.lower() if listone == "classic" else joined


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
            "SELECT q.stagione, q.player_id, p.nome, q.squadra, q.ruoli_codice, "
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
            role=_role_string(codes, listone),
            qi=qi,
            qa=qa,
            fvm=fvm,
        )
        for stagione, player_id, nome, squadra, codes, qi, qa, fvm in rows
    ]


def upsert_qi_bias(handle: Session, listone: str, rows: list[dict[str, object]]) -> int:
    """Write derived bias rows back. Idempotent.

    ``role`` arrives in the source files' spelling; it is normalised back to the
    upper-case array the table holds, so this round-trips through
    ``_role_string`` without changing what is stored.
    """
    if not rows:
        return 0

    payload = [
        {
            **row,
            "listone": listone,
            "ruoli_codice": split_codes(str(row.pop("role"))),
        }
        for row in rows
    ]

    handle.execute(
        text(
            "INSERT INTO qi_bias (stagione, player_id, listone, squadra, ruoli_codice, "
            "qi, qa, fvm, delta, pct_delta) VALUES (:stagione, :player_id, :listone, "
            ":squadra, :ruoli_codice, :qi, :qa, :fvm, :delta, :pct_delta) "
            "ON CONFLICT (stagione, player_id, listone) DO UPDATE SET "
            "squadra = EXCLUDED.squadra, ruoli_codice = EXCLUDED.ruoli_codice, "
            "qi = EXCLUDED.qi, qa = EXCLUDED.qa, fvm = EXCLUDED.fvm, "
            "delta = EXCLUDED.delta, pct_delta = EXCLUDED.pct_delta"
        ),
        payload,
    )
    return len(payload)


def upsert_target_price(
    handle: Session, listone: str, stagione: str, rows: list[dict[str, object]]
) -> int:
    """Write computed auction prices back. Idempotent.

    ``stagione`` is passed in because the derivation itself never carried one
    — historically it lived in a filename — and the table makes it a real
    NOT NULL column so a second season can coexist with this one.
    """
    if not rows:
        return 0

    payload = [
        {
            **row,
            "listone": listone,
            "stagione": stagione,
            "ruoli_codice": split_codes(str(row.pop("role"))),
            # split_flags, not split_codes: these are opaque strings from
            # target_price.py and upper-casing them would turn
            # team_discount(MIL) into something that no longer matches the
            # script that emits it. 122 live rows depend on that casing.
            "flags": split_flags(str(row.pop("flags"))),
        }
        for row in rows
    ]

    handle.execute(
        text(
            "INSERT INTO target_price (stagione, player_id, listone, squadra, "
            "ruoli_codice, macro_role, qi, prior_media_fantavoto, "
            "predicted_pct_delta, team_factor, target_price, flags) VALUES ("
            ":stagione, :player_id, :listone, :squadra, :ruoli_codice, :macro_role, "
            ":qi, :prior_media_fantavoto, :predicted_pct_delta, :team_factor, "
            ":target_price, :flags) "
            "ON CONFLICT (stagione, player_id, listone) DO UPDATE SET "
            "squadra = EXCLUDED.squadra, ruoli_codice = EXCLUDED.ruoli_codice, "
            "macro_role = EXCLUDED.macro_role, qi = EXCLUDED.qi, "
            "prior_media_fantavoto = EXCLUDED.prior_media_fantavoto, "
            "predicted_pct_delta = EXCLUDED.predicted_pct_delta, "
            "team_factor = EXCLUDED.team_factor, "
            "target_price = EXCLUDED.target_price, flags = EXCLUDED.flags"
        ),
        payload,
    )
    return len(payload)


def upsert_quotazioni(handle: Session, rows: list[dict[str, object]]) -> int:
    """Write scraped valuations. Idempotent on (stagione, player_id, listone).

    Dimensions first: ``players`` and ``teams`` must hold every id and club the
    facts reference, or the foreign keys reject the batch. The scraper knows
    both — it scraped them — so it seeds them in the same transaction rather
    than requiring a separate import first.
    """
    if not rows:
        return 0

    handle.execute(
        text(
            "INSERT INTO players (id, nome) VALUES (:player_id, :nome) "
            "ON CONFLICT (id) DO UPDATE SET nome = EXCLUDED.nome"
        ),
        [{"player_id": r["player_id"], "nome": r["nome"]} for r in rows],
    )
    handle.execute(
        text(
            "INSERT INTO teams (stagione, codice, nome_completo) "
            "VALUES (:stagione, :squadra, :squadra) "
            "ON CONFLICT (stagione, codice) DO NOTHING"
        ),
        [{"stagione": r["stagione"], "squadra": r["squadra"]} for r in rows],
    )
    handle.execute(
        text(
            "INSERT INTO quotazioni (stagione, player_id, listone, squadra, "
            "ruoli_codice, ruoli, qi, qa, fvm) VALUES (:stagione, :player_id, "
            ":listone, :squadra, :ruoli_codice, :ruoli, :qi, :qa, :fvm) "
            "ON CONFLICT (stagione, player_id, listone) DO UPDATE SET "
            "squadra = EXCLUDED.squadra, ruoli_codice = EXCLUDED.ruoli_codice, "
            "ruoli = EXCLUDED.ruoli, qi = EXCLUDED.qi, qa = EXCLUDED.qa, "
            "fvm = EXCLUDED.fvm"
        ),
        rows,
    )
    return len(rows)


def upsert_statistiche(handle: Session, rows: list[dict[str, object]]) -> int:
    """Write scraped season totals. Idempotent on (stagione, fonte, player_id, listone).

    ``media_voto``/``media_fantavoto`` arrive already parsed, so the ``"0,0"``
    no-data marker has been collapsed to ``None`` before it reaches here — the
    distinction SPEC criterion 9 is about.
    """
    if not rows:
        return 0

    handle.execute(
        text(
            "INSERT INTO players (id, nome) VALUES (:player_id, :nome) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        [{"player_id": r["player_id"], "nome": r["nome"]} for r in rows],
    )
    handle.execute(
        text(
            "INSERT INTO teams (stagione, codice, nome_completo) "
            "VALUES (:stagione, :squadra, :squadra) "
            "ON CONFLICT (stagione, codice) DO NOTHING"
        ),
        [{"stagione": r["stagione"], "squadra": r["squadra"]} for r in rows],
    )
    handle.execute(
        text(
            "INSERT INTO statistiche (stagione, fonte, player_id, listone, squadra, "
            "ruoli_codice, ruoli, partite_giocate, media_voto, media_fantavoto, gol, "
            "gol_subiti, rigori_segnati, rigori_tirati, rigori_parati, assist, "
            "ammonizioni, espulsioni) VALUES (:stagione, :fonte, :player_id, :listone, "
            ":squadra, :ruoli_codice, :ruoli, :partite_giocate, :media_voto, "
            ":media_fantavoto, :gol, :gol_subiti, :rigori_segnati, :rigori_tirati, "
            ":rigori_parati, :assist, :ammonizioni, :espulsioni) "
            "ON CONFLICT (stagione, fonte, player_id, listone) DO UPDATE SET "
            "squadra = EXCLUDED.squadra, ruoli_codice = EXCLUDED.ruoli_codice, "
            "ruoli = EXCLUDED.ruoli, partite_giocate = EXCLUDED.partite_giocate, "
            "media_voto = EXCLUDED.media_voto, "
            "media_fantavoto = EXCLUDED.media_fantavoto, gol = EXCLUDED.gol, "
            "gol_subiti = EXCLUDED.gol_subiti, "
            "rigori_segnati = EXCLUDED.rigori_segnati, "
            "rigori_tirati = EXCLUDED.rigori_tirati, "
            "rigori_parati = EXCLUDED.rigori_parati, assist = EXCLUDED.assist, "
            "ammonizioni = EXCLUDED.ammonizioni, espulsioni = EXCLUDED.espulsioni"
        ),
        rows,
    )
    return len(rows)


def upsert_match_grain(handle: Session, voti: list[dict], bonus: list[dict]) -> tuple[int, int]:
    """Write one matchday's rows to both match-grain tables.

    Two passes per table, one per partial unique index: a single INSERT names
    exactly one conflict target, and these tables have two — rows with a player
    and coach rows, which are disjoint. Repeating each index's predicate is what
    makes the statement legal, not an optimisation.
    """
    from fantabot.db.models.matches import BonusMalus, Voto
    from fantabot.db.upserts import upsert_two_passes

    if voti:
        handle.execute(
            text(
                "INSERT INTO players (id, nome) VALUES (:player_id, :nome) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            [
                {"player_id": r["player_id"], "nome": r["nome"]}
                for r in voti
                if r["player_id"] is not None
            ],
        )
        upsert_two_passes(
            handle,
            Voto,
            voti,
            updatable=(
                "data", "ora", "squadra_raw", "avversario_raw", "gol_squadra",
                "gol_avversario", "nome", "ruolo_codice", "ruolo", "voto_fc",
                "fantavoto_fc", "voto_stat", "fantavoto_stat", "voto_italia",
                "fantavoto_italia",
            ),
        )
    if bonus:
        upsert_two_passes(
            handle,
            BonusMalus,
            bonus,
            updatable=(
                "data", "squadra_raw", "avversario_raw", "nome", "ruolo_codice",
                "ruolo", "ammonizione", "espulsione", "gol_segnati", "gol_subiti",
                "autoreti", "rigori_segnati", "rigori_sbagliati", "rigori_parati",
                "assist", "mvp",
            ),
        )
    return len(voti), len(bonus)


def backfill_team_names(handle: Session) -> int:
    """Resolve teams.nome_completo from the two vocabularies already in Postgres.

    Thin pass-through to ReferenceRepository so the scrapers have one name to
    call and do not each grow their own copy.
    """
    from fantabot.db.repositories.reference import ReferenceRepository

    return ReferenceRepository(handle).backfill_team_names()


def resolve_team_names_or_report() -> int:
    """Run the backfill, reporting rather than failing on an unresolved mapping.

    Called at the end of both scrapers. A mapping failure must not mark a
    *successful* scrape as failed: by the time this runs the rows are already
    committed, and exiting non-zero would say the scrape did not happen. The
    operator's remedy is the standalone entry point below, once that season's
    fixtures exist.
    """
    from fantabot.club_names import TeamMappingError

    try:
        with session() as handle:
            changed = backfill_team_names(handle)
    except TeamMappingError as exc:
        print(
            f"teams: names not resolved ({exc}) — run "
            "`python scripts/_db.py backfill-team-names` once that season's "
            "fixtures are scraped"
        )
        return 0
    if changed:
        print(f"teams: resolved {changed} club name(s)")
    return changed


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "backfill-team-names":
        with session() as _handle:
            print(f"resolved {backfill_team_names(_handle)} club name(s)")
    else:
        print("usage: python scripts/_db.py backfill-team-names")
        raise SystemExit(2)
