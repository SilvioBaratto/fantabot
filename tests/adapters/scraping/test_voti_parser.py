"""`GiornataParser`, against a real giornata page rather than a page a test author wrote.

`adapters/scraping/voti.py` is 509 lines and `handle_starttag` is the most complex function
in the repository (C901 25). Until now nothing executed it. The failure it documents —
"page structure may have changed", answered with `SystemExit(1)` — is exactly the failure a
hand-built fixture cannot catch, because a hand-built page encodes the test author's reading
of the parser instead of the site's markup.

So the input here is `tests/fixtures/scraping/voti-2024-25-g24.html.gz`: the real
`/voti-fantacalcio-serie-a/2024-25/24`, recorded 2026-09-24, 1,234,833 bytes. See that
directory's README for its provenance and for the cross-check against `match_grain` that
makes it a fixture rather than saved bytes. 2024/25 on purpose: a completed season does not
move, so the page keeps meaning one thing.

**Nothing here opens a socket.** `fetch_giornata` takes an `html=` argument and is always
given one; `fetch_html` is never called. `QuotazioniParser`/`StatisticheParser` have no such
seam, which is why their tests drive the parser objects directly — this module does not need
to, and uses the public entry point.

**What is asserted is behaviour, never bytes.** Counts, one named player's whole row, and
the shape of each edge case the page actually contains. Retegui 2024/25 g24 is the anchor:
voto 9, four goals, MVP, fantavoto 21 — the maximum fantavoto in all 52,324 recorded rows,
so it is also the row that pins `FANTAVOTO_GLITCH_THRESHOLD` from below.

⚠ **Two assertions pin a defect rather than a behaviour**, and say so where they sit:
`team`/`opponent` are the *home* and *away* sides of the match, not the player's side and
his opponent. See `TestTheMatchHeader`. They are written as the parser behaves, because the
brief for this file is to pin what is there and report what is wrong — not to fix it.
"""

from __future__ import annotations

import gzip
from collections import Counter
from datetime import date, time
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from _paths import FIXTURES
from sqlalchemy import text
from sqlalchemy.orm import Session

from fantabot.adapters.scraping.voti import (
    BONUS_KEYS,
    FANTAVOTO_GLITCH_THRESHOLD,
    ROLE_LABELS,
    PlayerMatchRow,
    count_giornata,
    fetch_giornata,
    giornata_url,
    max_giornata,
    normalize_grade,
    to_payloads,
)

#: The recorded page, and the season/giornata it is.
PAGE = FIXTURES / "scraping" / "voti-2024-25-g24.html.gz"
SEASON = "2024/25"
GIORNATA = 24

#: Measured when the fixture was recorded, and equal to what `match_grain` holds for
#: 2024/25 g24 — see `TestAgainstTheCorpus`, which re-measures it rather than trusting this.
EXPECTED_ROWS = 337
#: Of those, the ones with a `player_id`. The other 20 are the coaches.
EXPECTED_WITH_ID = 317
EXPECTED_COACHES = 20

#: Atalanta's centre-forward, 2024/25 giornata 24: four goals and the MVP against Verona.
RETEGUI = "6228"
#: A substitute whose six grades all arrive from the site as the bare string "55".
PALESTRA = "6832"
#: The one player on the page whose three providers disagree three ways, on both halves.
GONZALEZ = "4179"
#: Fiorentina's keeper at Inter, and the only non-zero `gol_subiti` this file names.
DE_GEA = "2521"
#: Fiorentina's centre-back in the same match: the page's own goal that is not a conceded one.
PONGRACIC = "5603"
#: Roma's number ten at Venezia, and one of the giornata's four penalties.
DYBALA = "309"

#: Every bonus column of the recorded page, summed over all 337 rows.
#:
#: This exists because every *other* bonus assertion in this file is either a key-presence
#: check or Retegui's single row — and six of his eight cells are zero, which is what the
#: whole malus half of the table already looks like. Summing the column is what notices a
#: page change that keeps the shape and changes the values.
#:
#: ⚠ **Two of these eight are zero on this giornata** — nobody missed a penalty and nobody
#: saved one. `rigori_sbagliati` and `rigori_parati` therefore cannot tell "read correctly
#: as zero" from "dropped on the floor", and carry none of the coverage the other six do.
#: They are here so the mapping is the whole of `BONUS_KEYS` and a reader does not have to
#: work out which keys were left out; they are not evidence.
EXPECTED_BONUS_TOTALS = {
    "gol_segnati": 20,
    "gol_subiti": 27,
    "autoreti": 3,
    "rigori_segnati": 4,
    "rigori_sbagliati": 0,
    "rigori_parati": 0,
    "assist": 17,
    "mvp": 10,
}


@cache
def _html() -> str:
    return gzip.decompress(Path(PAGE).read_bytes()).decode("utf-8")


@cache
def _rows() -> tuple[PlayerMatchRow, ...]:
    """The whole page, parsed once for the module. `fetch_giornata` is the public path and
    the one `fetch_season` uses, so driving it also covers the final `_flush_row`."""
    return tuple(fetch_giornata(SEASON, GIORNATA, html=_html()))


def _by_id(player_id: str) -> PlayerMatchRow:
    found = [row for row in _rows() if row.player_id == player_id]
    assert len(found) == 1, f"{player_id} appears {len(found)} times on the page"
    return found[0]


class TestThePageAsAWhole:
    def test_every_player_and_coach_becomes_exactly_one_row(self) -> None:
        """337, which is what `match_grain` holds for this giornata. The count is the
        cheapest thing that fails when the row selector stops matching — and the last row
        of the last team table only exists because `fetch_giornata` flushes after `feed`."""
        assert len(_rows()) == EXPECTED_ROWS

    def test_the_twenty_team_tables_are_all_walked(self) -> None:
        """Ten matches, both sides of each. Counted through the players rather than the
        markup: 20 goalkeepers and 20 coaches is one of each per team table, so a table
        the parser skipped shows up here as 19."""
        roles = Counter(row.role_code for row in _rows())

        assert roles == {"c": 122, "d": 108, "a": 67, "p": 20, "all": 20}

    def test_every_row_carries_all_six_grades(self) -> None:
        """The pill index restarts at every player. If it did not, the second row in a
        table would be reading pills 3, 4 and 5 and half its columns would be empty.

        ⚠ "every row" is a property of *this* giornata, not of the page format: a postponed
        or not-yet-graded matchday renders its team tables with the grades empty, and the
        parser is right to return them so — `count_giornata` exists to count exactly that.
        Left as-is because the input is a completed 2024/25 giornata and cannot become one,
        but a reader re-pointing this file at a live page should expect it, and should not
        read the failure as a markup change.
        """
        ungraded = [
            row.name
            for row in _rows()
            if not all(
                (
                    row.voto_fc,
                    row.fantavoto_fc,
                    row.voto_stat,
                    row.fantavoto_stat,
                    row.voto_italia,
                    row.fantavoto_italia,
                )
            )
        ]

        assert ungraded == []

    def test_every_row_carries_all_eight_bonus_columns(self) -> None:
        """The bonus keys come from the span's `title`, not its position, so a column the
        site reorders is still read — and one it renames vanishes silently. This is what
        makes that loud.

        ⚠ Loud only when the rename takes the old `title` off the page. A rename that leaves
        a zeroed span behind under the old title — which is what a site does when the layout
        wants the cell — keeps all eight keys on all 337 rows and passes here with the column
        emptied. `TestTheBonusColumns` is what catches that one; this test is key presence
        and nothing more."""
        missing = {row.name: sorted(set(BONUS_KEYS) - set(row.bonus)) for row in _rows()}

        assert {name: keys for name, keys in missing.items() if keys} == {}
        assert set(_by_id(RETEGUI).bonus) == set(BONUS_KEYS)

    def test_the_season_and_giornata_asked_for_are_stamped_on_every_row(self) -> None:
        stamps = {(row.season, row.giornata) for row in _rows()}

        assert stamps == {(SEASON, GIORNATA)}

    def test_max_giornata_reads_the_season_length_off_the_select(self) -> None:
        """38 for a 20-team Serie A season — read from the page's own "Giornata" options
        rather than hardcoded, because `fetch_season` walks 2..max on the strength of it."""
        assert max_giornata(_html()) == 38

    def test_a_page_with_no_giornata_select_raises_rather_than_guessing(self) -> None:
        with pytest.raises(ValueError, match="Giornata"):
            max_giornata("<html><body>nothing here</body></html>")

    def test_the_url_spells_the_season_the_way_the_site_does(self) -> None:
        """The season is `2024/25` everywhere in this repository and `2024-25` in the
        path. This is the one address the recorded fixture came from, so it is also what
        a re-record has to ask for."""
        assert giornata_url(SEASON, GIORNATA) == (
            "https://www.fantacalcio.it/voti-fantacalcio-serie-a/2024-25/24"
        )


class TestOneNamedRow:
    def test_reteguis_row_is_read_field_for_field(self) -> None:
        """The anchor of this file. Four goals, the MVP, and fantavoto 21 — the largest
        fantavoto in the whole 52,324-row corpus, so it is also the live evidence that
        `normalize_grade` must leave a bare "21" alone.

        ⚠ `team`/`opponent` read Verona/Atalanta and he plays *for* Atalanta. That is the
        defect `TestTheMatchHeader` documents, pinned here as the parser behaves.
        """
        row = _by_id(RETEGUI)

        assert row.name == "Retegui"
        assert row.role_code == "a"
        assert row.role_label == "Attaccante"
        assert (row.team, row.opponent) == ("Verona", "Atalanta")
        assert (row.goals_for, row.goals_against) == ("0", "5")
        assert (row.date, row.time) == ("08/02/2025", "15:00")
        assert (row.voto_fc, row.fantavoto_fc) == ("9", "21")
        assert (row.voto_stat, row.fantavoto_stat) == ("9", "21")
        assert (row.voto_italia, row.fantavoto_italia) == ("9", "21")
        assert (row.ammonizione, row.espulsione) == (False, False)
        assert row.bonus == {
            "gol_segnati": "4",
            "gol_subiti": "0",
            "autoreti": "0",
            "rigori_segnati": "0",
            "rigori_sbagliati": "0",
            "rigori_parati": "0",
            "assist": "0",
            "mvp": "1",
        }


class TestTheBonusColumns:
    """The eight icon columns read as *values*, which nothing else in this file does.

    `test_every_row_carries_all_eight_bonus_columns` checks that every key is present, and
    `TestOneNamedRow` reads Retegui's eight — of which `gol_segnati=4` and `mvp=1` are the
    only two that are not zero. So between them they pin the key set and two cells, and the
    entire malus half of the table is unasserted: `gol_subiti`, `autoreti`,
    `rigori_segnati`, `rigori_sbagliati` and `rigori_parati` are zero on Retegui's row and
    zero on 320-odd of the other 336, which is what a zeroed column also looks like.

    A per-column total is the cheap thing that notices a column whose *shape* survives and
    whose values do not — a decorative twin span that overwrites the real one (the parser
    keys on `title` and takes the last write), a malus rendered signed, or two titles that
    swap. Every one of those keeps 337 rows, keeps all eight keys, and keeps Retegui exact.

    The named rows underneath are not redundant with the total: a total says *that*
    something moved, and one named row says *what*. They are also the three cells a total
    cannot localise, one per column that the total actually covers with a non-zero number.
    """

    def test_each_bonus_column_totals_what_the_page_holds(self) -> None:
        """The whole bonus table as eight numbers. See `EXPECTED_BONUS_TOTALS` for how they
        were measured and for the warning that two of the eight are zero and prove nothing.

        Summed the way `to_payloads` converts — a blank cell is 0 — so these are the eight
        column sums that would land in `match_grain` for this giornata, not a parser-only
        reading of them."""
        totals: Counter[str] = Counter()
        for row in _rows():
            for key in BONUS_KEYS:
                raw = row.bonus.get(key, "")
                totals[key] += int(raw) if raw.strip() else 0

        assert dict(totals) == EXPECTED_BONUS_TOTALS

    def test_a_named_keeper_carries_the_goals_he_conceded(self) -> None:
        """`gol_subiti` is a malus, and De Gea let Inter's two in. Fifteen rows on this page
        carry a non-zero one and until now not one of them was named, so the column could be
        zeroed whole and only Retegui's six zeros would have been there to say otherwise."""
        de_gea = _by_id(DE_GEA)

        assert de_gea.name == "De Gea"
        assert de_gea.role_code == "p"
        assert de_gea.bonus["gol_subiti"] == "2"

    def test_a_named_own_goal_is_not_read_as_a_conceded_one(self) -> None:
        """Both are malus columns, both are rendered by the same span with only the `title`
        telling them apart, and they sit side by side. Pongracic's own goal against Inter is
        asserted with his `gol_subiti` — he is an outfield player and has none — so a pair of
        crossed tooltips fails here and not only in the totals."""
        pongracic = _by_id(PONGRACIC)

        assert pongracic.name == "Pongracic"
        assert (pongracic.bonus["autoreti"], pongracic.bonus["gol_subiti"]) == ("1", "0")

    def test_a_named_penalty_is_read_into_the_penalty_column(self) -> None:
        """One of the giornata's four. `gol_segnati` is asserted alongside it because the
        site counts a converted penalty in `rigori_segnati` *only* — Dybala's goal at Venezia
        leaves his `gol_segnati` at 0 — so a column folded into its neighbour shows up as a
        1 where this expects a 0."""
        dybala = _by_id(DYBALA)

        assert dybala.name == "Dybala"
        assert (dybala.bonus["rigori_segnati"], dybala.bonus["gol_segnati"]) == ("1", "0")


class TestTheThreeProviders:
    """Redazione Fantacalcio, Voto Statistico and Voto Italia are three separate columns.

    They agree on almost every row, which is precisely the hazard: a parser that collapsed
    two of them would look right on 333 of 337 rows. Nicolas Gonzalez is the row where all
    three disagree on both halves, so it is the one that can tell them apart.
    """

    def test_all_three_base_votes_are_read_into_their_own_column(self) -> None:
        row = _by_id(GONZALEZ)

        assert (row.voto_fc, row.voto_stat, row.voto_italia) == ("6", "6,5", "5,5")

    def test_all_three_fantavoti_are_read_into_their_own_column(self) -> None:
        row = _by_id(GONZALEZ)

        assert (row.fantavoto_fc, row.fantavoto_stat, row.fantavoto_italia) == (
            "7",
            "7,5",
            "6,5",
        )

    def test_the_base_vote_and_the_fantavoto_of_one_provider_are_not_the_same_cell(
        self,
    ) -> None:
        """Gonzalez's assist is the difference between the two halves of every pill."""
        row = _by_id(GONZALEZ)

        assert row.bonus["assist"] == "1"
        assert row.voto_fc != row.fantavoto_fc

    def test_the_providers_disagree_on_exactly_four_rows(self) -> None:
        """A floor on how much of the page is actually three-valued. If a mutation made
        two providers share a column, 333 rows would still look fine and this would not."""
        three_ways = [
            row
            for row in _rows()
            if len({row.voto_fc, row.voto_stat, row.voto_italia}) == 3
        ]

        assert sorted(row.player_id for row in three_ways) == [
            "4179",
            "4530",
            "5421",
            "5998",
        ]


class TestThePlayerId:
    def test_the_id_is_the_last_numeric_segment_of_the_player_link(self) -> None:
        """`.../squadre/atalanta/palestra/6832/2024-25` — the trailing season is not a
        digit run, so the id is found by walking the path backwards."""
        assert _by_id(RETEGUI).player_id == RETEGUI
        assert _by_id(PALESTRA).name == "Palestra"

    def test_every_id_on_the_page_is_a_bare_integer(self) -> None:
        with_id = [row.player_id for row in _rows() if row.player_id]

        assert len(with_id) == EXPECTED_WITH_ID
        assert all(value.isdigit() for value in with_id)
        assert len(set(with_id)) == EXPECTED_WITH_ID, "a player is listed twice"

    def test_the_twenty_rows_with_no_id_are_the_coaches(self) -> None:
        """Coaches render as a bare `<span class="player-name">` with no link at all. They
        are real rows — they carry a grade — and `match_grain` holds them with a NULL
        `player_id`, which is why the table has two partial unique indexes."""
        idless = [row for row in _rows() if not row.player_id]

        assert len(idless) == EXPECTED_COACHES
        assert {row.role_code for row in idless} == {"all"}
        assert {row.role_label for row in idless} == {"Allenatore"}

    def test_a_named_coach_row_is_complete_apart_from_the_id(self) -> None:
        """Pinned by name so "the ids stopped being dropped" and "the ids started being
        invented" are different failures. Gasperini is Atalanta's coach, in Atalanta's
        table — the header still reads Verona, for the reason `TestTheMatchHeader` gives."""
        gasperini = [row for row in _rows() if row.name == "Gasperini"]

        assert len(gasperini) == 1
        assert gasperini[0].player_id == ""
        assert gasperini[0].role_code == "all"
        assert gasperini[0].voto_fc == "7,5"
        assert gasperini[0].date == "08/02/2025"


class TestTheCards:
    """A booking is a CSS class on the grade span, not one of the eight bonus columns.

    fantacalcio.it scores ammonizione and espulsione as real malus categories but renders
    them as `player-grade yellow-card` / `player-grade red-card`, so they are read off the
    class list and written into the bonus payload alongside the eight icon columns.
    """

    def test_the_booked_players_are_exactly_the_yellow_card_spans(self) -> None:
        booked = [row for row in _rows() if row.ammonizione]

        assert len(booked) == 34
        assert _by_id("6024").name == "Sulemana I."
        assert _by_id("6024").ammonizione is True

    def test_the_sent_off_players_are_exactly_the_red_card_spans(self) -> None:
        """Two on this giornata, both in Empoli-Milan."""
        sent_off = {row.player_id for row in _rows() if row.espulsione}

        assert sent_off == {"6809", "4751"}

    def test_a_red_card_is_not_also_read_as_a_yellow(self) -> None:
        """The two classes are separate `if`s on the same span, so a swapped or widened
        test would set both. Neither sent-off player was booked first on this page."""
        marianucci = _by_id("6809")

        assert (marianucci.ammonizione, marianucci.espulsione) == (False, True)
        assert marianucci.name == "Marianucci"

    def test_the_unbooked_majority_carries_neither_flag(self) -> None:
        clean = [row for row in _rows() if not row.ammonizione and not row.espulsione]

        assert len(clean) == EXPECTED_ROWS - 34 - 2


class TestTheMatchHeader:
    """The `<header>` of each team table: "Verona 0 - 5 Atalanta" and "08/02/2025 - 15:00".

    ⚠ **The two sides are recorded home-first, for both tables of a match.** The page marks
    the table's own side with `class="current"` on two of the five score spans, and the
    parser does not read it: it takes span 0 as `team` and span 4 as `opponent` whichever
    table it is in. So an away player's `team` is his opponent's name, his `goals_for` are
    the goals against him, and a 20-team giornata yields 10 distinct `team` values.

    This is a defect, and the corpus already holds it: `match_grain` stores Retegui under
    `squadra_raw='Verona'` with `gol_squadra=0`, and **every giornata of all five seasons
    holds exactly 10 distinct `squadra_raw` values, never 20** — measured over all 52,324
    rows, with `squadra_raw` and `avversario_raw` never once overlapping inside a giornata.

    ⚠ It is not a one-line fix, which is why it is reported and not made. The one consumer
    of those columns, `LineupHistoryRepository.fixtures`, selects them `.distinct()` and
    names them `home`/`away` — it is *correct today* precisely because both tables of a
    match agree, and would start returning each fixture twice, mirrored, the moment the
    parser recorded the player's own side. What is wrong is the column naming
    (`squadra_raw`/`gol_squadra` read as the player's team) and `count_giornata` below.

    The tests here pin what the parser does, which is the brief.
    """

    def test_the_kickoff_is_split_on_the_spaced_dash(self) -> None:
        """One `<div class="match-date ml-auto">` per table holds both halves. The plain
        `match-date` in the page's top navigation is not this one, and `ml-auto` is what
        tells them apart."""
        row = _by_id(RETEGUI)

        assert row.date == "08/02/2025"
        assert row.time == "15:00"

    def test_every_row_on_the_page_carries_a_kickoff(self) -> None:
        assert [row.name for row in _rows() if not row.date or not row.time] == []

    def test_the_nine_kickoff_slots_of_the_giornata_are_all_read(self) -> None:
        """Ten matches across nine distinct slots — two played Sunday at 20:45 is one
        slot, not two. A header read once and then reused for the rest of the page would
        collapse this to one."""
        slots = {(row.date, row.time) for row in _rows()}

        assert slots == {
            ("07/02/2025", "20:45"),
            ("08/02/2025", "15:00"),
            ("08/02/2025", "18:00"),
            ("08/02/2025", "20:45"),
            ("09/02/2025", "12:30"),
            ("09/02/2025", "15:00"),
            ("09/02/2025", "18:00"),
            ("09/02/2025", "20:45"),
            ("10/02/2025", "20:45"),
        }

    def test_the_score_is_read_as_two_separate_counters(self) -> None:
        row = _by_id(RETEGUI)

        assert (row.goals_for, row.goals_against) == ("0", "5")

    def test_both_sides_of_a_match_are_recorded_under_the_home_team(self) -> None:
        """⚠ Pinning a defect. Retegui and Gasperini are Atalanta's; the parser files them
        under Verona because Verona is named first in the score header of *both* tables."""
        assert _by_id(RETEGUI).team == "Verona"
        assert _by_id(RETEGUI).opponent == "Atalanta"

        teams = {row.team for row in _rows()}
        opponents = {row.opponent for row in _rows()}

        assert len(teams) == 10, "a 20-team giornata should not yield 10 distinct teams"
        assert teams & opponents == set(), "no side is ever recorded as both"

    def test_count_giornata_over_the_real_page_halves_the_fixture_count(self) -> None:
        """⚠ Pinning the consequence. `count_giornata` counts distinct `row.team`, so a
        fully played ten-match giornata reports **five** fixtures and ten teams where its
        own docstring promises twenty listed and ten played.

        Contained, and worth saying so: nothing in production reads `teams_listed`,
        `teams_graded` or `fixtures` today, and the freshness verdict that would care takes
        its per-giornata count from `LineupHistoryRepository.fixtures` instead, which is
        right. So this is a latent wrong answer, not a live one — and the reason it went
        unnoticed is that the only tests these fields had built their rows by hand."""
        count = count_giornata(GIORNATA, list(_rows()), len(_rows()))

        assert count.teams_listed == 10
        assert count.teams_graded == 10
        assert count.fixtures == 5


class TestRoleLabel:
    def test_every_code_on_the_page_resolves_to_its_italian_label(self) -> None:
        labels = {row.role_code: row.role_label for row in _rows()}

        assert labels == {
            "p": "Portiere",
            "d": "Difensore",
            "c": "Centrocampista",
            "a": "Attaccante",
            "all": "Allenatore",
        }
        assert labels == {code: ROLE_LABELS[code] for code in labels}

    def test_a_code_the_table_does_not_know_is_returned_unchanged(self) -> None:
        """A new role would otherwise become an empty `ruolo` column in `match_grain`."""
        row = PlayerMatchRow(season=SEASON, giornata=GIORNATA, role_code="zz")

        assert row.role_label == "zz"


class TestNormalizeGrade:
    """The site bug this function exists for, and the boundary that keeps it from eating
    real scores.

    On some *subentrato* rows fantacalcio.it drops the decimal comma, so "5,5" renders as a
    bare "55". It is genuinely the site's bug, and it is not rare: 144 of this one page's
    2,022 grade cells are the bare string "55".
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("55", "5,5"), ("10", "1,0"), ("21", "2,1"), ("99", "9,9"), ("30", "3,0")],
    )
    def test_any_bare_two_digit_base_voto_is_the_glitch(self, raw: str, expected: str) -> None:
        """A base voto is a grade before bonus and malus, so it never legitimately reaches
        two digits — verified at 9.50 maximum across all 52,324 recorded rows. There is
        therefore no threshold on this branch, and "21" really is 2,1 here."""
        assert normalize_grade(raw, is_base_voto=True) == expected

    @pytest.mark.parametrize("raw", ["10", "13", "14", "21", "29", "-10", "-29"])
    def test_a_fantavoto_below_the_threshold_is_a_real_score_and_is_kept(
        self, raw: str
    ) -> None:
        """Bonus stacking reaches two digits legitimately: this very page carries 10, 13,
        14 and Retegui's 21. Mangling those would be a hundredfold error in the corpus."""
        assert normalize_grade(raw, is_base_voto=False) == raw

    @pytest.mark.parametrize(
        ("raw", "expected"), [("30", "3,0"), ("55", "5,5"), ("99", "9,9")]
    )
    def test_a_fantavoto_at_or_above_the_threshold_is_the_glitch(
        self, raw: str, expected: str
    ) -> None:
        assert normalize_grade(raw, is_base_voto=False) == expected

    def test_the_threshold_is_the_boundary_and_it_is_inclusive(self) -> None:
        """29 is the highest fantavoto left alone, 30 the lowest treated as the glitch.
        Both signs, because `BARE_TWO_DIGIT_RE` captures the minus separately and an
        off-by-one here is nine real scores wide."""
        assert FANTAVOTO_GLITCH_THRESHOLD == 30
        assert normalize_grade("29", is_base_voto=False) == "29"
        assert normalize_grade("30", is_base_voto=False) == "3,0"
        assert normalize_grade("-29", is_base_voto=False) == "-29"
        assert normalize_grade("-30", is_base_voto=False) == "-3,0"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("-55", "-5,5"), ("-30", "-3,0"), ("-99", "-9,9")],
    )
    def test_the_minus_sign_survives_the_repair(self, raw: str, expected: str) -> None:
        """A malus-heavy fantavoto is negative — the corpus floor is -2.00 — and a repair
        that dropped the sign would turn a punishment into a reward."""
        assert normalize_grade(raw, is_base_voto=False) == expected
        assert normalize_grade(raw, is_base_voto=True) == expected

    @pytest.mark.parametrize(
        "raw", ["5,5", "10,0", "-1,5", "", "-", "6.5", "5", "100", "6,25", "5 5"]
    )
    @pytest.mark.parametrize("is_base", [True, False])
    def test_anything_that_is_not_a_bare_two_digit_string_is_returned_unchanged(
        self, raw: str, is_base: bool
    ) -> None:
        """Including "6.5": a dot decimal is not this function's business, and
        `italian_decimal` refuses it downstream rather than reading it as 65."""
        assert normalize_grade(raw, is_base_voto=is_base) == raw

    def test_the_repair_is_visible_on_the_recorded_page(self) -> None:
        """Palestra's six cells all arrive as the bare string "55" and all six come out as
        5,5 — while Retegui's "21" on the same page comes out as 21. One page, both
        branches, which is what makes the threshold a measurement rather than a guess."""
        assert _by_id(PALESTRA).voto_fc == "5,5"
        assert _by_id(PALESTRA).fantavoto_fc == "5,5"
        assert _by_id(PALESTRA).voto_italia == "5,5"
        assert _by_id(PALESTRA).fantavoto_italia == "5,5"
        assert _by_id(RETEGUI).fantavoto_fc == "21"


class TestToPayloads:
    """Fixture to upsert row, which is the whole of what `store_giornata` writes."""

    def _payload_pair(self, player_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        voti_rows, bonus_rows = to_payloads([_by_id(player_id)])
        return voti_rows[0], bonus_rows[0]

    def test_reteguis_voti_payload_is_the_whole_column_mapping(self) -> None:
        """Every scalar column of `match_grain`'s grade half, typed as the model declares
        it: `data` a date read the Italian way, `ora` a time, the six grades `Decimal`."""
        payload, _ = self._payload_pair(RETEGUI)

        assert payload == {
            "stagione": "2024/25",
            "giornata": 24,
            "data": date(2025, 2, 8),
            "ora": time(15, 0),
            "squadra_raw": "Verona",
            "avversario_raw": "Atalanta",
            "player_id": 6228,
            "nome": "Retegui",
            "ruolo_codice": "A",
            "ruolo": "Attaccante",
            "gol_squadra": 0,
            "gol_avversario": 5,
            "voto_fc": Decimal("9"),
            "fantavoto_fc": Decimal("21"),
            "voto_stat": Decimal("9"),
            "fantavoto_stat": Decimal("21"),
            "voto_italia": Decimal("9"),
            "fantavoto_italia": Decimal("21"),
        }

    def test_reteguis_bonus_payload_is_the_whole_counter_mapping(self) -> None:
        """The ten counters, all NOT NULL in the table: the two cards as 0/1 integers and
        the eight icon columns as they were on the page."""
        _, payload = self._payload_pair(RETEGUI)

        assert payload == {
            "stagione": "2024/25",
            "giornata": 24,
            "data": date(2025, 2, 8),
            "squadra_raw": "Verona",
            "avversario_raw": "Atalanta",
            "player_id": 6228,
            "nome": "Retegui",
            "ruolo_codice": "A",
            "ruolo": "Attaccante",
            "ammonizione": 0,
            "espulsione": 0,
            "gol_segnati": 4,
            "gol_subiti": 0,
            "autoreti": 0,
            "rigori_segnati": 0,
            "rigori_sbagliati": 0,
            "rigori_parati": 0,
            "assist": 0,
            "mvp": 1,
        }

    def test_the_bonus_payload_carries_no_kickoff_time(self) -> None:
        """`bonus_malus` never had one, and the merged table still writes the two halves
        separately for that reason."""
        _, payload = self._payload_pair(RETEGUI)

        assert "ora" not in payload
        assert "gol_squadra" not in payload

    def test_a_coach_row_becomes_a_null_player_id_and_an_all_code(self) -> None:
        """The half of `match_grain` that has no `player_id`, which is why the table
        carries two partial unique indexes instead of one."""
        gasperini = next(row for row in _rows() if row.name == "Gasperini")
        voti_rows, bonus_rows = to_payloads([gasperini])

        assert voti_rows[0]["player_id"] is None
        assert voti_rows[0]["ruolo_codice"] == "ALL"
        assert voti_rows[0]["ruolo"] == "Allenatore"
        assert bonus_rows[0]["player_id"] is None

    def test_a_booking_becomes_one_and_not_true(self) -> None:
        """`ammonizione` is a SmallInteger column, not a boolean."""
        _, payload = self._payload_pair("6024")

        assert payload["ammonizione"] == 1
        assert isinstance(payload["ammonizione"], int)
        assert not isinstance(payload["ammonizione"], bool)

    def test_a_missing_counter_is_zero_and_not_null(self) -> None:
        """Zero goals is zero goals; the ten counters are NOT NULL. A row whose bonus dict
        never got a key — a column the site drops — must still write 0."""
        bare = PlayerMatchRow(season=SEASON, giornata=GIORNATA, date="08/02/2025")
        _, bonus_rows = to_payloads([bare])
        payload = bonus_rows[0]

        assert all(payload[key] == 0 for key in BONUS_KEYS)

    def test_an_empty_scoreline_is_zero(self) -> None:
        bare = PlayerMatchRow(season=SEASON, giornata=GIORNATA, date="08/02/2025")
        voti_rows, _ = to_payloads([bare])

        assert (voti_rows[0]["gol_squadra"], voti_rows[0]["gol_avversario"]) == (0, 0)
        assert voti_rows[0]["ora"] is None

    def test_the_whole_page_converts_row_for_row(self) -> None:
        """337 in, 337 and 337 out — `upsert_match_grain` zips the two lists, so a length
        that drifted would silently pair a player's grades with another's bonuses."""
        voti_rows, bonus_rows = to_payloads(list(_rows()))

        assert len(voti_rows) == EXPECTED_ROWS
        assert len(bonus_rows) == EXPECTED_ROWS
        assert [row["nome"] for row in voti_rows] == [row["nome"] for row in bonus_rows]
        assert sum(1 for row in voti_rows if row["player_id"] is None) == EXPECTED_COACHES


#: These read what has actually been scraped, so they run against the canonical database
#: rather than the tier's own empty one — inside the same rolled-back transaction. They are
#: what turns the two numbers above into measurements: `FANTAVOTO_GLITCH_THRESHOLD` is a
#: claim about every row ever recorded, and the fixture's 337 is a claim about this giornata.
@pytest.mark.db
@pytest.mark.dbdata
class TestAgainstTheCorpus:

    def test_no_recorded_fantavoto_comes_near_the_glitch_threshold(
        self, db_session: Session
    ) -> None:
        """What makes 30 a measurement. The docstring says real fantavoti "top out at ~21";
        this is that sentence, re-measured on every run against all three provider columns.

        It turns red the day a real score would be mangled — the margin is nine points, so
        a rules change that added a bonus category is what would consume it.
        """
        row = db_session.execute(
            text(
                "SELECT max(fantavoto_fc), max(fantavoto_stat), max(fantavoto_italia), "
                "count(*) FILTER (WHERE fantavoto_fc >= :t OR fantavoto_stat >= :t "
                "OR fantavoto_italia >= :t), count(*) FROM match_grain"
            ),
            {"t": FANTAVOTO_GLITCH_THRESHOLD},
        ).one()
        best_fc, best_stat, best_italia, at_threshold, total = row

        assert total > 50_000, "the corpus is not loaded; this would pass over nothing"
        assert at_threshold == 0
        assert max(best_fc, best_stat, best_italia) < FANTAVOTO_GLITCH_THRESHOLD

    def test_no_recorded_base_voto_reaches_two_digits(self, db_session: Session) -> None:
        """The other half of the docstring, and the reason the base-voto branch needs no
        threshold at all. A base voto of 10 would make every such row a 1,0.

        The premise is exercised and not merely described: the corpus maximum, rendered the
        way the site's glitch renders it, must come back out of `normalize_grade` as exactly
        that maximum. So the measurement and the repair are asserted against each other,
        and a repair that transposed or dropped a digit fails here as well as upstairs.
        """
        best, two_digit = db_session.execute(
            text(
                "SELECT max(voto_fc), count(*) FILTER (WHERE voto_fc >= 10 "
                "OR voto_stat >= 10 OR voto_italia >= 10) FROM match_grain"
            )
        ).one()

        assert two_digit == 0
        assert best < 10

        glitched = f"{best:.1f}".replace(".", "")
        repaired = normalize_grade(glitched, is_base_voto=True)
        assert Decimal(repaired.replace(",", ".")) == best

    def test_the_fixture_parses_the_same_number_of_rows_the_corpus_holds(
        self, db_session: Session
    ) -> None:
        """The fixture is only a fixture because these two agree. If a re-record disagrees,
        the parser changed behaviour — that is the signal, not a stale file."""
        stored, with_id = db_session.execute(
            text(
                "SELECT count(*), count(player_id) FROM match_grain "
                "WHERE stagione = :s AND giornata = :g"
            ),
            {"s": SEASON, "g": GIORNATA},
        ).one()

        assert stored == len(_rows()) == EXPECTED_ROWS
        assert with_id == sum(1 for row in _rows() if row.player_id) == EXPECTED_WITH_ID

    def test_reteguis_parsed_row_agrees_with_the_one_that_was_stored(
        self, db_session: Session
    ) -> None:
        """End to end against the real write path: this page, through `to_payloads`, is
        what put that row there. The maximum fantavoto in the corpus, on both sides."""
        payload, bonus = to_payloads([_by_id(RETEGUI)])
        stored = db_session.execute(
            text(
                "SELECT nome, voto_fc, fantavoto_fc, gol_segnati, assist, mvp, "
                "squadra_raw, avversario_raw FROM match_grain "
                "WHERE stagione = :s AND giornata = :g AND player_id = :p"
            ),
            {"s": SEASON, "g": GIORNATA, "p": int(RETEGUI)},
        ).one()

        assert stored.nome == payload[0]["nome"]
        assert stored.voto_fc == payload[0]["voto_fc"]
        assert stored.fantavoto_fc == payload[0]["fantavoto_fc"]
        assert (stored.gol_segnati, stored.assist, stored.mvp) == (
            bonus[0]["gol_segnati"],
            bonus[0]["assist"],
            bonus[0]["mvp"],
        )
        # ⚠ The stored row carries the home side too, so the defect is in the corpus and
        # not only in the parser. Asserted rather than described, so a fix fails here.
        assert (stored.squadra_raw, stored.avversario_raw) == ("Verona", "Atalanta")
