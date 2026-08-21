"""T4: the player universe, joined across the two quotazioni files.

One news CSV serves both leagues, so every row carries the Classic role and the
Mantra tag side by side. That means a join, and a join means a way to be wrong:
an id in one file and not the other must raise, never quietly produce a row with
an empty Mantra tag — that column is the whole Mantra half of the feature.
"""

from pathlib import Path

import pytest

from fantabot.news.pool import PoolJoinError, PoolPlayer, load_pool

CLASSIC_HEADER = "stagione,id,nome,squadra,ruolo_codice,ruolo,qi,qa,fvm\n"
MANTRA_HEADER = "stagione,id,nome,squadra,ruoli_codice,ruoli,qi,qa,fvm\n"


def _write(path: Path, header: str, *rows: str) -> Path:
    path.write_text(header + "".join(row + "\n" for row in rows), encoding="utf-8")
    return path


def _pair(tmp_path: Path, classic: list[str], mantra: list[str]) -> tuple[Path, Path]:
    return (
        _write(tmp_path / "classic.csv", CLASSIC_HEADER, *classic),
        _write(tmp_path / "mantra.csv", MANTRA_HEADER, *mantra),
    )


def test_join_carries_both_roles(tmp_path: Path) -> None:
    classic, mantra = _pair(
        tmp_path,
        ["2026/27,6916,Ahanor,ATA,d,Difensore,10,10,25"],
        ["2026/27,6916,Ahanor,ATA,DD;DC,Dd;Dc,10,10,25"],
    )

    pool = load_pool(classic, mantra, season="2026/27")

    assert pool == [
        PoolPlayer(
            id="6916",
            nome="Ahanor",
            squadra="ATA",
            ruolo="Difensore",
            ruoli_mantra="DD;DC",
        )
    ]


def test_other_seasons_are_filtered_out(tmp_path: Path) -> None:
    classic, mantra = _pair(
        tmp_path,
        [
            "2025/26,177,Ilicic,ATA,a,Attaccante,11,10,0",
            "2026/27,6916,Ahanor,ATA,d,Difensore,10,10,25",
        ],
        [
            "2025/26,177,Ilicic,ATA,A,Attaccante,11,10,0",
            "2026/27,6916,Ahanor,ATA,DD;DC,Dd;Dc,10,10,25",
        ],
    )

    pool = load_pool(classic, mantra, season="2026/27")

    assert [p.id for p in pool] == ["6916"]


def test_the_season_is_a_parameter_not_a_constant(tmp_path: Path) -> None:
    classic, mantra = _pair(
        tmp_path,
        ["2025/26,177,Ilicic,ATA,a,Attaccante,11,10,0"],
        ["2025/26,177,Ilicic,ATA,A,Attaccante,11,10,0"],
    )

    assert [p.id for p in load_pool(classic, mantra, season="2025/26")] == ["177"]


def test_a_player_missing_from_the_mantra_file_raises(tmp_path: Path) -> None:
    classic, mantra = _pair(
        tmp_path,
        [
            "2026/27,6916,Ahanor,ATA,d,Difensore,10,10,25",
            "2026/27,4521,Zaccagni,LAZ,c,Centrocampista,18,18,90",
        ],
        ["2026/27,6916,Ahanor,ATA,DD;DC,Dd;Dc,10,10,25"],
    )

    with pytest.raises(PoolJoinError) as excinfo:
        load_pool(classic, mantra, season="2026/27")

    message = str(excinfo.value)
    assert "4521" in message
    assert "2" in message and "1" in message  # both counts named


def test_a_player_missing_from_the_classic_file_raises(tmp_path: Path) -> None:
    classic, mantra = _pair(
        tmp_path,
        ["2026/27,6916,Ahanor,ATA,d,Difensore,10,10,25"],
        [
            "2026/27,6916,Ahanor,ATA,DD;DC,Dd;Dc,10,10,25",
            "2026/27,4521,Zaccagni,LAZ,W;T,W;T,18,18,90",
        ],
    )

    with pytest.raises(PoolJoinError):
        load_pool(classic, mantra, season="2026/27")


def test_an_empty_season_raises_rather_than_returning_nothing(tmp_path: Path) -> None:
    # A silent empty pool would make a cron run look successful while doing nothing.
    classic, mantra = _pair(tmp_path, [], [])

    with pytest.raises(PoolJoinError):
        load_pool(classic, mantra, season="2026/27")


def test_the_pool_is_ordered_deterministically(tmp_path: Path) -> None:
    # Resume, logs and diffs all depend on a stable order across runs.
    classic, mantra = _pair(
        tmp_path,
        [
            "2026/27,4521,Zaccagni,LAZ,c,Centrocampista,18,18,90",
            "2026/27,6916,Ahanor,ATA,d,Difensore,10,10,25",
        ],
        [
            "2026/27,4521,Zaccagni,LAZ,W;T,W;T,18,18,90",
            "2026/27,6916,Ahanor,ATA,DD;DC,Dd;Dc,10,10,25",
        ],
    )

    assert [p.nome for p in load_pool(classic, mantra, season="2026/27")] == [
        "Ahanor",
        "Zaccagni",
    ]


_DATA = Path(__file__).resolve().parent.parent / "data"
REAL_CLASSIC = _DATA / "quotazioni_classic.csv"
REAL_MANTRA = _DATA / "quotazioni_mantra.csv"


@pytest.mark.skipif(
    not (REAL_CLASSIC.exists() and REAL_MANTRA.exists()),
    reason="data/ is gitignored; this check only runs where the scraped files exist",
)
def test_the_real_files_join_to_523_players() -> None:
    pool = load_pool(REAL_CLASSIC, REAL_MANTRA, season="2026/27")

    assert len(pool) == 523
    assert all(p.ruoli_mantra for p in pool)
