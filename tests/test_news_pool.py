"""The player universe, joined across the two listoni.

One sentiment series serves both leagues, so every row carries the Classic role
and the Mantra tag side by side. That means a join, and a join means a way to be
wrong: an id in one listone and not the other must raise, never quietly produce
a row with an empty Mantra tag — that column is the whole Mantra half of the
feature.

The rows come from ``quotazioni`` now rather than from two CSVs, but
``build_pool`` is still pure, so these stay in the socket-free tier and test the
join itself rather than the fetch. Season filtering moved into the query, so it
is asserted in tests/integration/ alongside the end-to-end check that the
database produces the same 523 players in the same order.
"""

import pytest

from fantabot.data_sources.models import QuotazioneRow
from fantabot.domain.news.pool import PoolJoinError, PoolPlayer, build_pool


def _q(player_id: str, nome: str, squadra: str, ruolo: str, codici: str) -> QuotazioneRow:
    return QuotazioneRow(
        player_id=player_id,
        nome=nome,
        squadra=squadra,
        ruoli_codice=tuple(code for code in codici.split(";") if code),
        ruoli=(ruolo,) if ruolo else (),
    )


def _pair(
    classic: list[QuotazioneRow], mantra: list[QuotazioneRow]
) -> tuple[dict[str, QuotazioneRow], dict[str, QuotazioneRow]]:
    return ({r.player_id: r for r in classic}, {r.player_id: r for r in mantra})


AHANOR_C = _q("6916", "Ahanor", "ATA", "Difensore", "D")
AHANOR_M = _q("6916", "Ahanor", "ATA", "Dd;Dc", "DD;DC")


def test_the_join_carries_both_role_systems() -> None:
    classic, mantra = _pair([AHANOR_C], [AHANOR_M])

    assert build_pool(classic, mantra, "2026/27") == [
        PoolPlayer(
            id="6916",
            nome="Ahanor",
            squadra="ATA",
            ruolo="Difensore",
            ruoli_mantra="DD;DC",
        )
    ]


def test_the_classic_role_keeps_the_casing_a_human_wrote() -> None:
    """It goes straight into the prompt. "DIFENSORE" is not what the source says."""
    classic, mantra = _pair([AHANOR_C], [AHANOR_M])

    assert build_pool(classic, mantra, "2026/27")[0].ruolo == "Difensore"


def test_a_player_missing_from_mantra_raises() -> None:
    """Nulling the tag would ship a row whose ruoli_mantra is empty."""
    classic, mantra = _pair([AHANOR_C], [])

    with pytest.raises(PoolJoinError, match="only in classic"):
        build_pool(classic, mantra, "2026/27")


def test_a_player_missing_from_classic_raises() -> None:
    classic, mantra = _pair([], [AHANOR_M])

    with pytest.raises(PoolJoinError, match="only in mantra"):
        build_pool(classic, mantra, "2026/27")


def test_the_error_names_the_offending_ids() -> None:
    classic, mantra = _pair([AHANOR_C], [])

    with pytest.raises(PoolJoinError) as excinfo:
        build_pool(classic, mantra, "2026/27")

    assert "6916" in str(excinfo.value)


def test_an_empty_season_raises_rather_than_returning_nothing() -> None:
    """A silent empty pool would make a cron run look successful."""
    with pytest.raises(PoolJoinError, match="no players"):
        build_pool({}, {}, "2030/31")


def test_the_pool_is_ordered_by_club_then_name() -> None:
    """Resume, logs and diffs all depend on this being stable across runs."""
    rows = [
        ("2", "Zaccagni", "LAZ"),
        ("1", "Ahanor", "ATA"),
        ("3", "Bastoni", "INT"),
        ("4", "Acerbi", "INT"),
    ]
    classic, mantra = _pair(
        [_q(i, n, s, "Difensore", "D") for i, n, s in rows],
        [_q(i, n, s, "Dc", "DC") for i, n, s in rows],
    )

    pool = build_pool(classic, mantra, "2026/27")

    assert [(p.squadra, p.nome) for p in pool] == [
        ("ATA", "Ahanor"),
        ("INT", "Acerbi"),
        ("INT", "Bastoni"),
        ("LAZ", "Zaccagni"),
    ]


def test_a_player_with_no_classic_role_does_not_crash_the_join() -> None:
    """Defensive: the column is NOT NULL, but an empty array would otherwise
    index-error rather than produce a visibly wrong row."""
    classic, mantra = _pair([_q("1", "Nobody", "ATA", "", "D")], [_q("1", "Nobody", "ATA", "Dc", "DC")])

    assert build_pool(classic, mantra, "2026/27")[0].ruolo == ""
