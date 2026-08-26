"""Parsing primitives for the ten source CSVs. Pure, no database.

The decimal separator is not consistent across the files, and the difference is
silent rather than loud. Measured on the real data:

    statistiche_*.csv   13222 comma-decimals, 0 dot, 2846 "0,0" sentinels
    voti.csv           102100 comma-decimals, 0 dot
    qi_bias_*.csv           0 comma-decimals, dot only
    target_price_*.csv      0 comma-decimals, dot only, 523 blanks

One shared parser therefore has to guess, and guessing wrong is not an error —
``Decimal("38.46".replace(",", "."))`` is fine, and ``"38,46"`` read as a plain
decimal is ``3846``. Two parsers that each refuse the other's format turn a
silent hundredfold error into a crash on the first row.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from fantabot.db.importers._csv import (
    italian_decimal,
    plain_decimal,
    split_codes,
    split_flags,
)
from fantabot.db.importers.matches import chunked, parse_date, parse_time
from fantabot.db.importers.players import PlayerRef, read_refs, resolve_names
from fantabot.db.importers.qi_bias import read_rows as qi_bias_rows
from fantabot.db.importers.quotazioni import read_rows as quotazioni_rows
from fantabot.db.importers.statistiche import read_rows as statistiche_rows
from fantabot.db.importers.target_price import SeasonNotInFilenameError, season_from_filename
from fantabot.db.importers.target_price import read_rows as target_price_rows
from fantabot.db.importers.teams import TeamMappingError, build_mapping, code_for
from fantabot.db.importers.voti import read_rows as voti_rows


class TestItalianDecimal:
    """statistiche_*.csv and voti.csv."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("6,25", Decimal("6.25")),
            ("-8,6", Decimal("-8.6")),
            (" 6,5 ", Decimal("6.5")),
            ("6", Decimal("6")),
            ("0,5", Decimal("0.5")),
        ],
    )
    def test_parses_comma_decimals(self, raw: str, expected: Decimal) -> None:
        assert italian_decimal(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "0,0"])
    def test_no_data_sentinels_become_none(self, raw: str) -> None:
        """Empty is did-not-play; "0,0" is no-data. Both are absent, not zero —
        SPEC criterion 9 requires 2846 NULLs and no zeros in media_voto."""
        assert italian_decimal(raw) is None

    def test_zero_that_is_really_zero_survives(self) -> None:
        """A genuine 0 (not the "0,0" sentinel) must not be swallowed."""
        assert italian_decimal("0") == Decimal("0")

    def test_a_dot_decimal_raises_instead_of_multiplying_by_a_hundred(self) -> None:
        with pytest.raises(ValueError, match="dot"):
            italian_decimal("38.46")

    def test_garbage_raises(self) -> None:
        with pytest.raises(ValueError):
            italian_decimal("n/a")


class TestPlainDecimal:
    """qi_bias_*.csv and target_price_*.csv."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("38.46", Decimal("38.46")),
            ("-0.5", Decimal("-0.5")),
            (" 1.25 ", Decimal("1.25")),
            ("7", Decimal("7")),
        ],
    )
    def test_parses_dot_decimals(self, raw: str, expected: Decimal) -> None:
        assert plain_decimal(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_becomes_none(self, raw: str) -> None:
        assert plain_decimal(raw) is None

    def test_zero_point_zero_is_a_value_not_a_sentinel(self) -> None:
        """Unlike the Italian files, these use a blank for no-data, so 0.0 is a
        real measurement — pct_delta can legitimately be zero."""
        assert plain_decimal("0.0") == Decimal("0.0")

    def test_a_comma_decimal_raises_instead_of_guessing(self) -> None:
        with pytest.raises(ValueError, match="comma"):
            plain_decimal("6,25")


class TestSplitCodes:
    """``;``-joined role codes become ``text[]``."""

    def test_splits_and_upper_cases(self) -> None:
        assert split_codes("B;DS;E") == ["B", "DS", "E"]

    def test_normalises_case_and_whitespace(self) -> None:
        """quotazioni_mantra stores uppercase, mantra_schemi.json mixed case,
        and the rules doc lowercase. Something has to normalise."""
        assert split_codes(" b ; Dc ;pc ") == ["B", "DC", "PC"]

    def test_a_single_code_becomes_a_one_element_list(self) -> None:
        """Classic shares the Mantra column, holding one element."""
        assert split_codes("P") == ["P"]

    def test_empty_becomes_an_empty_list_not_a_list_containing_empty(self) -> None:
        assert split_codes("") == []
        assert split_codes(";;") == []


def test_the_parsers_do_not_reach_for_a_database() -> None:
    """This module is imported by every importer and must stay pure, so its
    tests never need the db marker."""
    from fantabot.db.importers import _csv as module

    assert module.__file__ is not None
    text = Path(module.__file__).read_text()
    assert "sqlalchemy" not in text
    assert "fantabot.db.engine" not in text


class TestPlayerNameResolution:
    """94 of the 1474 ids spell their name more than one way across seasons, so
    the seed needs a rule that does not depend on which file was read first."""

    @staticmethod
    def _ref(player_id: int, nome: str, stagione: str, rank: int) -> PlayerRef:
        return PlayerRef(player_id=player_id, nome=nome, stagione=stagione, source_rank=rank)

    def test_the_most_recent_season_wins(self) -> None:
        refs = [
            self._ref(1, "SORIANO", "2022/23", 0),
            self._ref(1, "Soriano", "2026/27", 0),
        ]
        assert resolve_names(refs) == {1: "Soriano"}

    def test_the_result_does_not_depend_on_input_order(self) -> None:
        """A seed that varied with file order would be unreproducible."""
        refs = [
            self._ref(1, "Soriano", "2026/27", 0),
            self._ref(1, "SORIANO", "2022/23", 0),
        ]
        assert resolve_names(refs) == {1: "Soriano"}
        assert resolve_names(list(reversed(refs))) == {1: "Soriano"}

    def test_within_one_season_the_more_canonical_source_wins(self) -> None:
        """quotazioni (rank 0) over voti (rank 2) for the same season."""
        refs = [
            self._ref(1, "From voti", "2026/27", 2),
            self._ref(1, "From quotazioni", "2026/27", 0),
        ]
        assert resolve_names(refs) == {1: "From quotazioni"}

    def test_every_distinct_id_survives(self) -> None:
        refs = [self._ref(i, f"P{i}", "2026/27", 0) for i in range(5)]
        assert len(resolve_names(refs)) == 5

    def test_no_refs_is_an_empty_mapping_not_an_error(self) -> None:
        assert resolve_names([]) == {}


class TestPlayerRefReading:
    def test_rows_without_an_id_are_skipped(self, tmp_path: Path) -> None:
        """3039 coach rows per match-grain file carry an empty id."""
        csv_path = tmp_path / "voti.csv"
        csv_path.write_text(
            "stagione,id,nome\n2026/27,7,Real Player\n2026/27,,Allenatore\n",
            encoding="utf-8",
        )

        refs = list(read_refs(tmp_path))

        assert [ref.player_id for ref in refs] == [7]

    def test_a_missing_source_file_is_not_an_error(self, tmp_path: Path) -> None:
        assert list(read_refs(tmp_path)) == []


class TestTeamMappingIsFailClosed:
    """A partial mapping is worse than no import: a NULL nome_completo makes
    later joins return zero rows while every table still looks populated."""

    def test_the_happy_path_maps_every_code(self) -> None:
        mapping = build_mapping(["Fiorentina", "Milan", "Atalanta"], ["FIO", "MIL", "ATA"])

        assert mapping == {"FIO": "Fiorentina", "MIL": "Milan", "ATA": "Atalanta"}

    def test_a_prefix_collision_raises_instead_of_picking_one(self) -> None:
        with pytest.raises(TeamMappingError, match="not unique"):
            build_mapping(["Milan", "Milano"], ["MIL"])

    def test_a_code_with_no_full_name_raises(self) -> None:
        with pytest.raises(TeamMappingError, match="no full name"):
            build_mapping(["Fiorentina"], ["FIO", "XYZ"])

    def test_the_error_names_the_unresolved_codes(self) -> None:
        with pytest.raises(TeamMappingError) as excinfo:
            build_mapping(["Fiorentina"], ["FIO", "ABC", "DEF"])

        assert "ABC" in str(excinfo.value)
        assert "DEF" in str(excinfo.value)

    def test_a_full_name_with_no_code_is_allowed(self) -> None:
        """Relegated clubs still appear in older voti rows. Extra names are
        harmless; missing ones are not."""
        assert build_mapping(["Fiorentina", "Salernitana"], ["FIO"]) == {
            "FIO": "Fiorentina",
            "SAL": "Salernitana",
        }

    def test_code_for_upper_cases_the_first_three_letters(self) -> None:
        assert code_for("Fiorentina") == "FIO"
        assert code_for(" udinese ") == "UDI"


class TestQuotazioniRows:
    """Classic and Mantra differ only in the role column name, so one reader
    handles both and the listone is what distinguishes the rows."""

    @staticmethod
    def _write(tmp_path: Path) -> None:
        (tmp_path / "quotazioni_classic.csv").write_text(
            "stagione,id,nome,squadra,ruolo_codice,ruolo,qi,qa,fvm\n"
            "2026/27,7,Tizio,ata,P,Portiere,12,13,40\n",
            encoding="utf-8",
        )
        (tmp_path / "quotazioni_mantra.csv").write_text(
            "stagione,id,nome,squadra,ruoli_codice,ruoli,qi,qa,fvm\n"
            "2026/27,7,Tizio,ATA,B;DS;E,Braccetto;Dif.;Esterno,12,13,40\n",
            encoding="utf-8",
        )

    def test_both_files_land_with_their_own_listone(self, tmp_path: Path) -> None:
        self._write(tmp_path)

        rows = quotazioni_rows(tmp_path)

        assert sorted(row["listone"] for row in rows) == ["classic", "mantra"]

    def test_classic_roles_become_a_one_element_array(self, tmp_path: Path) -> None:
        self._write(tmp_path)

        classic = next(r for r in quotazioni_rows(tmp_path) if r["listone"] == "classic")

        assert classic["ruoli_codice"] == ["P"]

    def test_mantra_roles_are_split_on_semicolons(self, tmp_path: Path) -> None:
        self._write(tmp_path)

        mantra = next(r for r in quotazioni_rows(tmp_path) if r["listone"] == "mantra")

        assert mantra["ruoli_codice"] == ["B", "DS", "E"]
        assert mantra["ruoli"] == ["BRACCETTO", "DIF.", "ESTERNO"]

    def test_the_club_code_is_upper_cased_to_match_teams(self, tmp_path: Path) -> None:
        """quotazioni_classic writes 'ata' in this fixture; the composite
        foreign key to teams.codice would miss it uncorrected."""
        self._write(tmp_path)

        classic = next(r for r in quotazioni_rows(tmp_path) if r["listone"] == "classic")

        assert classic["squadra"] == "ATA"

    def test_a_missing_file_contributes_nothing(self, tmp_path: Path) -> None:
        assert quotazioni_rows(tmp_path) == []


class TestStatisticheRows:
    """The no-data marker is the whole risk here: "0,0" must not become 0."""

    @staticmethod
    def _write(tmp_path: Path, media: str) -> None:
        header = (
            "stagione,fonte,id,nome,squadra,ruolo_codice,ruolo,partite_giocate,"
            "media_voto,media_fantavoto,gol,gol_subiti,rigori_segnati,rigori_tirati,"
            "rigori_parati,assist,ammonizioni,espulsioni\n"
        )
        # Comma-decimals must be quoted, exactly as the real file writes them —
        # unquoted, "6,25" is two CSV fields and the whole row shifts left.
        (tmp_path / "statistiche_classic.csv").write_text(
            header
            + f'2026/27,fantacalcio,7,Tizio,ATA,C,Centrocampista,30,"{media}","6,25",'
            "25,0,0,0,0,3,4,0\n",
            encoding="utf-8",
        )

    def test_the_no_data_marker_becomes_null_not_zero(self, tmp_path: Path) -> None:
        """2846 rows carry "0,0". Stored as 0 they would drag every average
        that reads this table toward zero, and nothing would look wrong."""
        self._write(tmp_path, "0,0")

        assert statistiche_rows(tmp_path)[0]["media_voto"] is None

    def test_a_real_average_is_parsed_as_a_decimal(self, tmp_path: Path) -> None:
        self._write(tmp_path, "6,25")

        assert statistiche_rows(tmp_path)[0]["media_voto"] == Decimal("6.25")

    def test_counters_are_integers_and_a_zero_counter_stays_zero(
        self, tmp_path: Path
    ) -> None:
        """Unlike the averages, a counter that says zero means zero."""
        self._write(tmp_path, "6,25")
        row = statistiche_rows(tmp_path)[0]

        assert row["partite_giocate"] == 30
        assert row["rigori_segnati"] == 0
        assert row["gol"] == 25

    def test_the_grading_source_is_carried_into_the_key(self, tmp_path: Path) -> None:
        """Three fonte values publish different averages for the same player,
        so the grain is four-way rather than three."""
        self._write(tmp_path, "6,25")

        assert statistiche_rows(tmp_path)[0]["fonte"] == "fantacalcio"


class TestQiBiasRows:
    """Dot-decimal territory: the file that would be silently multiplied by a
    hundred if it went through the Italian parser."""

    @staticmethod
    def _write(tmp_path: Path) -> None:
        (tmp_path / "qi_bias_classic.csv").write_text(
            "stagione,id,nome,squadra,role,qi,qa,fvm,delta,pct_delta\n"
            "2022/23,2832,Boga,ATA,a,13,18,13,5,38.46\n",
            encoding="utf-8",
        )

    def test_the_dot_decimal_keeps_its_magnitude(self, tmp_path: Path) -> None:
        """38.46, not 3846 — the error a single shared parser would make."""
        self._write(tmp_path)

        assert qi_bias_rows(tmp_path)[0]["pct_delta"] == Decimal("38.46")

    def test_the_italian_parser_would_have_rejected_this_file(self, tmp_path: Path) -> None:
        """States the reason the two parsers exist, against a real row."""
        with pytest.raises(ValueError, match="dot"):
            italian_decimal("38.46")

    def test_the_lower_cased_classic_role_is_normalised(self, tmp_path: Path) -> None:
        """qi_bias_classic writes 'a' where quotazioni_classic writes 'A'."""
        self._write(tmp_path)

        assert qi_bias_rows(tmp_path)[0]["ruoli_codice"] == ["A"]

    def test_delta_is_carried_through_as_an_integer(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        row = qi_bias_rows(tmp_path)[0]

        assert row["delta"] == 5
        assert row["delta"] == row["qa"] - row["qi"]


class TestTargetPriceRows:
    """The season is not in this file, and blank is not the same as zero."""

    @staticmethod
    def _write(tmp_path: Path, prior: str, predicted: str, flags: str) -> None:
        (tmp_path / "target_price_2026_27_classic.csv").write_text(
            "id,nome,squadra,role,macro_role,qi,prior_media_fantavoto,"
            "predicted_pct_delta,team_factor,target_price,flags\n"
            f"5995,De Ketelaere,ATA,a,ATT,17,{prior},{predicted},1.000,16,{flags}\n",
            encoding="utf-8",
        )

    def test_the_season_comes_from_the_filename(self, tmp_path: Path) -> None:
        self._write(tmp_path, "6.53", "-8.6", "")

        assert target_price_rows(tmp_path)[0]["stagione"] == "2026/27"

    def test_an_unreadable_filename_raises_rather_than_guessing(self) -> None:
        """Rows written under the wrong season stay invisible until a second
        season lands on top of the first."""
        with pytest.raises(SeasonNotInFilenameError):
            season_from_filename("target_price_classic.csv")

    def test_a_blank_prediction_is_null(self, tmp_path: Path) -> None:
        self._write(tmp_path, "", "", "")
        row = target_price_rows(tmp_path)[0]

        assert row["prior_media_fantavoto"] is None
        assert row["predicted_pct_delta"] is None

    def test_a_prediction_of_zero_survives_as_zero(self, tmp_path: Path) -> None:
        """One real row reads "+0.0" — a prediction of no change, not a missing
        value. Collapsing it to NULL, as the Italian parser does with "0,0",
        would lose a genuine forecast."""
        self._write(tmp_path, "6.16", "+0.0", "")

        assert target_price_rows(tmp_path)[0]["predicted_pct_delta"] == Decimal("0.0")

    def test_no_flags_is_an_empty_array_never_null(self, tmp_path: Path) -> None:
        self._write(tmp_path, "6.53", "-8.6", "")

        assert target_price_rows(tmp_path)[0]["flags"] == []

    def test_flag_casing_is_preserved(self, tmp_path: Path) -> None:
        """Unlike role codes. team_discount(MIL) upper-cased no longer matches
        what scripts/target_price.py emits."""
        self._write(tmp_path, "6.53", "-8.6", "thin_prior_sample_no_fade;team_discount(MIL)")

        assert target_price_rows(tmp_path)[0]["flags"] == [
            "thin_prior_sample_no_fade",
            "team_discount(MIL)",
        ]


class TestSplitFlags:
    def test_case_is_preserved_unlike_role_codes(self) -> None:
        assert split_flags("floor_qi;team_discount(MIL)") == [
            "floor_qi",
            "team_discount(MIL)",
        ]
        assert split_codes("floor_qi") == ["FLOOR_QI"]

    def test_empty_is_an_empty_list(self) -> None:
        assert split_flags("") == []


class TestMatchGrainParsing:
    def test_dates_are_read_in_italian_order(self) -> None:
        """01/02/2025 is 1 February, not 2 January. Read the American way,
        every match in the first twelve days of a month lands in the wrong one."""
        assert parse_date("01/02/2025") == date(2025, 2, 1)

    def test_a_kick_off_time_is_parsed(self) -> None:
        assert parse_time("12:30") == time(12, 30)

    def test_a_missing_kick_off_time_is_none(self) -> None:
        """bonus_malus carries no time at all; voti carries one for every row."""
        assert parse_time("") is None
        assert parse_time("   ") is None

    def test_chunking_covers_every_row_exactly_once(self) -> None:
        rows = [{"n": i} for i in range(4501)]

        batches = list(chunked(rows, size=2000))

        assert [len(batch) for batch in batches] == [2000, 2000, 501]
        assert [row["n"] for batch in batches for row in batch] == list(range(4501))


class TestVotiRows:
    @staticmethod
    def _write(tmp_path: Path, player_id: str, ruolo_codice: str) -> None:
        (tmp_path / "voti.csv").write_text(
            "stagione,giornata,data,ora,squadra,avversario,gol_squadra,gol_avversario,"
            "id,nome,ruolo_codice,ruolo,voto_fc,fantavoto_fc,voto_stat,fantavoto_stat,"
            "voto_italia,fantavoto_italia\n"
            f'2024/25,3,01/02/2025,12:30,Atalanta,Bologna,2,1,{player_id},Tizio,'
            f'{ruolo_codice},Centrocampista,"6,25","7,25","6,0","7,0","6,5","7,5"\n',
            encoding="utf-8",
        )

    def test_a_coach_row_keeps_its_name_and_carries_no_player(self, tmp_path: Path) -> None:
        """3039 rows per file. A NOT NULL foreign key would reject all of them."""
        self._write(tmp_path, "", "all")
        row = voti_rows(tmp_path)[0]

        assert row["player_id"] is None
        assert row["nome"] == "Tizio"
        assert row["ruolo_codice"] == "ALL"

    def test_a_player_row_carries_its_id(self, tmp_path: Path) -> None:
        self._write(tmp_path, "7", "c")
        row = voti_rows(tmp_path)[0]

        assert row["player_id"] == 7
        assert row["ruolo_codice"] == "C"

    def test_the_corrupt_team_columns_are_carried_as_raw(self, tmp_path: Path) -> None:
        """Kept, but named so nobody mistakes them for the player's own club."""
        self._write(tmp_path, "7", "c")
        row = voti_rows(tmp_path)[0]

        assert row["squadra_raw"] == "Atalanta"
        assert row["avversario_raw"] == "Bologna"
        assert "squadra" not in row

    def test_the_fixture_score_is_kept(self, tmp_path: Path) -> None:
        """Four columns the plan never named. Row counts would not have caught
        their absence."""
        self._write(tmp_path, "7", "c")
        row = voti_rows(tmp_path)[0]

        assert (row["gol_squadra"], row["gol_avversario"]) == (2, 1)

    def test_grades_are_comma_decimals(self, tmp_path: Path) -> None:
        self._write(tmp_path, "7", "c")

        assert voti_rows(tmp_path)[0]["voto_fc"] == Decimal("6.25")
