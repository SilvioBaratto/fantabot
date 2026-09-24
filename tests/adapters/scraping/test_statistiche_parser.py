"""`StatisticheParser` against a recorded page, because the site is the input.

`adapters/scraping/statistiche.py` had no test at all, and the failure it documents --
"page structure may have changed", answered with `SystemExit(1)` -- is reached only when a
run yields *nothing*. Everything short of that is silent, so these tests pin the three
things a partial change moves: how many rows come off the page, one named player's whole
row, and which value is under which key.

The fixture is a real 2024/25 page, gzipped, recorded 2026-09-24;
`tests/fixtures/scraping/README.md` records its provenance and the cross-check against the
52,324 rows already in `match_grain`. A finished season on purpose: it does not move, so
the numbers below keep meaning one thing. Nothing here opens a socket -- `fetch_provider`
and `fetch_html` are never called and the parser is fed the bytes directly.

⚠ **The stats dict is keyed from each `<td data-col-key=...>`, not from the header row.**
The page carries a header (`<th data-col-key="mv">`) and a `<col>` element with the same
attribute, and the parser ignores both -- it reads `td` only, and only inside a
`player-row`. **So a column inserted upstream is dropped, not shifted**, because every
value travels with its own key and an unknown key is never captured at all. Measured
2026-09-24 rather than reasoned: inserting `<td data-col-key="xg">1,23</td>` before every
one of the 679 `pg` cells leaves the whole file green and every value under its own key.
`test_a_column_inserted_upstream_is_dropped_and_shifts_nothing` asks it of the parser
directly, because the recorded page cannot.

The live failure is the opposite one: a key **renamed or dropped** vanishes from all 679
rows at once, `run()`'s `counter("")` turns it into a 0, and nothing raises. That is what
`test_every_row_carries_every_column` is for.

⚠ **The third failure is neither: a cell that keeps its key and changes its meaning.**
That is what `test_each_key_is_bound_to_the_kind_of_value_its_column_renders` is for, and
it is the one the key-order test was wrongly credited with -- see its docstring. Two
page changes were measured landing there and passing everything else in this file: a
`data-col-key` mapping slipped one position (`pg` becomes `'ATA'`, `mv` becomes `'36'`),
and a stat cell growing a second figure (`<td data-col-key="pg">31 <small>(310')</small>`,
674 rows, `run()`'s `int()` raising for the whole run and this file silent).

⚠ **`handle_data` overwrites `name` and accumulates `stats`, and that asymmetry is a
hazard, not a design.** Only the last text node inside `<a class="player-name">` survives,
so the site wrapping part of a name in its own element corrupts it silently: a surname
span costs 138 of 679 names (the page already renders `Zapata D.`, `Martinez L.`,
`Sulemana I.`), and a status badge on injured players costs 55-57 at a realistic 8% rate.
Two tests bracket it rather than one, because the obvious repair -- accumulate like the
stats path -- is as unconstrained as the defect. The whole-page cross-check
(`test_every_name_is_the_whole_text_of_its_profile_link`) goes red on the page change and
green again on the repair; the two synthetic pins below it go red on the repair. Moving
in either direction is then a decision someone makes.

**Every number below is the database's too**, re-measured 2026-09-24 against
`statistiche` for `stagione='2024/25', fonte='italia', listone='classic'`:

| pinned here | the database says |
|---|---|
| 679 rows | 679 rows; and 679 distinct `quotazioni` players for the season |
| Retegui 6228 ATA, pg 36, mv 6,47, mfv 8,64, gol 25, rig 4/5, ass 7, amm 2 | the same row, with `rigori_segnati=4` and `rigori_tirati=5` |
| Maignan 4312, gs 41, rp 1, pg 37 | the same |
| 20 team codes | `count(distinct squadra) = 20` |
| 119 rows rendering `0,0` | `count(*) where media_voto is null = 119` |

That last pair is the one worth reading twice: the parser hands `0,0` through untouched
and the column is NULL in Postgres, so the collapse happens in `italian_decimal` and
nowhere else. A parser that started collapsing it too would make those 119 rows depend on
which of the two ran — and they agree today only because one of them abstains.
"""

from __future__ import annotations

import gzip
import re
from decimal import Decimal
from html import unescape

import pytest
from _paths import FIXTURES

from fantabot.adapters.scraping.statistiche import (
    CLASSIC_ROLES,
    MANTRA_ROLES,
    PROVIDERS,
    PlayerStatsRow,
    StatisticheParser,
    stats_url,
)
from fantabot.domain.shared.parsing import italian_decimal

PAGE = FIXTURES / "scraping" / "statistiche-2024-25-italia.html.gz"

#: The page's ten stat columns, in the order they are rendered. `sq` is deliberately not
#: here: it carries a `data-col-key` like the rest but the parser routes it to `team`.
STAT_KEYS = ("pg", "mv", "mfv", "gol", "gs", "rig", "rp", "ass", "amm", "esp")

#: The database holds 679 players for 2024/25 and the page renders 679 `player-row`s.
EXPECTED_ROWS = 679

#: What each column *renders*, as opposed to what it is called. Measured over all 679 rows
#: of the fixture: seven pure counters, two Italian decimals, one compound cell. The kinds
#: differ from one another on purpose -- that is what makes a value under the wrong key
#: visible at all.
#:
#: The `-?` on the averages is not observed here (the page's minimum is `0,0`); it is
#: allowed because a negative season fantavoto is a legitimate number and a test that
#: reddened for one would be a false alarm. It costs nothing: a rotated column lands a bare
#: integer or a three-letter team code in `mv`, and neither has a comma.
COLUMN_SHAPES = {
    "pg": r"\d+",
    "mv": r"-?\d+,\d+",
    "mfv": r"-?\d+,\d+",
    "gol": r"\d+",
    "gs": r"\d+",
    "rig": r"\d+ / \d+",
    "rp": r"\d+",
    "ass": r"\d+",
    "amm": r"\d+",
    "esp": r"\d+",
}

#: The name link, read a second way. `StatisticheParser` keeps one text node; this keeps
#: all of them, which is the whole point -- the two agree today and stop agreeing the
#: moment the site wraps part of a name in its own element.
NAME_LINK = re.compile(r'<a class="player-name player-link"[^>]*>(.*?)</a>', re.S)


def _visible_text(fragment: str) -> str:
    """Every text node of an HTML fragment, tags stripped and whitespace collapsed.

    `unescape` is not decoration: 20 of the 679 names carry `&#x27;` (`Soule'`,
    `Bernabe'`, `Kone' M.`), and `HTMLParser` converts charrefs before `handle_data`
    sees them. A reader that skipped it would report 20 false mismatches and the test
    would be re-written until it asserted nothing.
    """
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


#: Retegui's own cells, in the page's order, with the page's own indentation.
CELLS = "".join(
    f'<td data-col-key="{key}">\n{" " * 28}{value}\n{" " * 24}</td>\n'
    for key, value in (
        ("sq", "ATA"),
        ("pg", "36"),
        ("mv", "6,47"),
        ("mfv", "8,64"),
        ("gol", "25"),
        ("gs", "0"),
        ("rig", "4 / 5"),
        ("rp", "0"),
        ("ass", "7"),
        ("amm", "2"),
        ("esp", "0"),
    )
)

HREF = "https://www.fantacalcio.it/serie-a/squadre/atalanta/retegui/6228/2024-25/italia"


def _one_row(name_html: str, href: str = HREF, cells: str = CELLS) -> PlayerStatsRow:
    """One `player-row` fed straight to the parser.

    Synthetic, and only where the recorded page cannot ask the question: a name split
    across tags, a second all-digit segment in the href, a column the site has not
    inserted yet. Everything the page *can* answer is asked of the page.
    """
    parser = StatisticheParser("2024/25", "italia")
    parser.feed(
        '<tr class="player-row" data-filter-role-classic="a"'
        ' data-filter-role-mantra="pc">'
        '<th class="player-name">'
        f'<a class="player-name player-link" href="{href}">{name_html}</a>'
        f"</th>{cells}</tr>"
    )
    (row,) = parser.rows
    return row


def _parse(provider: str = "italia") -> list[PlayerStatsRow]:
    html = gzip.decompress(PAGE.read_bytes()).decode("utf-8")
    parser = StatisticheParser("2024/25", provider)
    parser.feed(html)
    return parser.rows


@pytest.fixture(scope="module")
def rows() -> list[PlayerStatsRow]:
    """Parsed once: the page is 1.9 MB of HTML and every test here reads the same rows."""
    return _parse()


@pytest.fixture(scope="module")
def by_name(rows: list[PlayerStatsRow]) -> dict[str, PlayerStatsRow]:
    return {row.name: row for row in rows}


class TestThePageAsAWhole:
    def test_the_recorded_page_yields_one_row_per_player(
        self, rows: list[PlayerStatsRow]
    ) -> None:
        """679 is the count the fixture was checked against: the database holds 679
        players for 2024/25, and the quotazioni page of the same season parses 679 too."""
        assert len(rows) == EXPECTED_ROWS

    def test_the_header_and_the_column_definitions_are_not_players(
        self, rows: list[PlayerStatsRow]
    ) -> None:
        """`data-col-key="mv"` appears 681 times on this page — once on a `<col>`, once on
        the sortable `<th>`, and 679 times on real cells. A parser that keyed on the
        attribute alone rather than on `td`-inside-`player-row` would report 681."""
        assert len(rows) == EXPECTED_ROWS
        assert all(row.name for row in rows)
        assert all(row.player_id.isdigit() for row in rows)

    def test_every_row_carries_every_column(self, rows: list[PlayerStatsRow]) -> None:
        """The partial collapse, which `run()` cannot see.

        `run()` refuses only an *empty* page. A `data-col-key` renamed upstream drops one
        column from all 679 rows, every one of them still parses, and `counter("")` writes
        a 0 — so the shortfall reaches Postgres as data. This is the only thing that
        notices.
        """
        incomplete = [
            (row.name, sorted(set(STAT_KEYS) - set(row.stats)))
            for row in rows
            if set(row.stats) != set(STAT_KEYS)
        ]

        assert incomplete == [], f"rows missing columns: {incomplete[:5]}"
        assert [row.name for row in rows if any(not v for v in row.stats.values())] == []

    def test_every_row_is_identified_and_placed(self, rows: list[PlayerStatsRow]) -> None:
        """A row with no id is dropped by `run()` before the upsert, and one with no team
        writes `squadra = ""`. On this page neither happens — all 679 are complete."""
        assert [row.name for row in rows if not row.player_id.isdigit()] == []
        assert [row.name for row in rows if not re.fullmatch(r"[A-Z]{3}", row.team)] == []
        assert len({row.team for row in rows}) == 20  # == count(distinct squadra)

    def test_every_role_code_is_one_the_module_can_name(
        self, rows: list[PlayerStatsRow]
    ) -> None:
        """`role_classic_label` falls back to the raw code rather than raising, so an
        unknown code would reach `ruoli` as a letter and never be noticed."""
        classic = {row.role_classic_code for row in rows}
        mantra = {code for row in rows for code in row.role_mantra_codes}

        assert classic == {"p", "d", "c", "a"}
        assert classic <= set(CLASSIC_ROLES)
        assert mantra <= set(MANTRA_ROLES)
        assert mantra == {"por", "dc", "b", "dd", "ds", "e", "m", "c", "w", "t", "a", "pc"}


class TestOneNamedRow:
    def test_reteguis_whole_row(self, by_name: dict[str, PlayerStatsRow]) -> None:
        """Retegui 2024/25 is the most discriminating row on the page: he is non-zero in
        `pg`, `mv`, `mfv`, `gol`, `rig` (both halves, and they differ), `ass` and `amm`,
        so a value that moves one column lands somewhere visible. He is also the corpus
        maximum — 21.00 fantavoto at g24 — which is what the glitch threshold is set
        against.
        """
        retegui = by_name["Retegui"]

        assert retegui.player_id == "6228"
        assert retegui.name == "Retegui"
        assert retegui.team == "ATA"
        assert retegui.role_classic_code == "a"
        assert retegui.role_mantra_codes == ["pc"]
        assert retegui.stats == {
            "pg": "36",
            "mv": "6,47",
            "mfv": "8,64",
            "gol": "25",
            "gs": "0",
            "rig": "4 / 5",
            "rp": "0",
            "ass": "7",
            "amm": "2",
            "esp": "0",
        }

    def test_the_id_comes_from_the_profile_link(
        self, by_name: dict[str, PlayerStatsRow]
    ) -> None:
        """The only place the id is on the page is the `href`
        `.../squadre/atalanta/retegui/6228/2024-25/italia`; the `<tr>`'s own
        `data-filter-*` attributes carry roles and keywords, never the id."""
        assert by_name["Retegui"].player_id == "6228"
        assert by_name["Venturino"].player_id == "6980"
        assert by_name["Maignan"].player_id == "4312"

    def test_a_goalkeepers_row_fills_the_columns_an_outfielder_leaves_at_zero(
        self, by_name: dict[str, PlayerStatsRow]
    ) -> None:
        """`gs` and `rp` are 0 for all but the keepers, so Retegui's row cannot tell them
        apart from each other or from a neighbour. Maignan's can: 41 conceded, 1 saved."""
        maignan = by_name["Maignan"]

        assert maignan.role_classic_code == "p"
        assert (maignan.stats["gs"], maignan.stats["rp"]) == ("41", "1")
        assert (maignan.stats["gol"], maignan.stats["ass"]) == ("0", "0")
        assert maignan.stats["pg"] == "37"

    def test_the_roles_are_read_from_the_rows_own_filter_attributes(
        self, by_name: dict[str, PlayerStatsRow]
    ) -> None:
        """Mantra roles are a `|`-joined list on the `<tr>`, and a player with two of them
        is the only row that says whether the split happened. Calhanoglu is `m|c`."""
        calhanoglu = by_name["Calhanoglu"]

        assert calhanoglu.role_classic_code == "c"
        assert calhanoglu.role_classic_label == "Centrocampista"
        assert calhanoglu.role_mantra_codes == ["m", "c"]
        assert calhanoglu.role_mantra_labels == ["Mediano", "Cen.centrale"]
        assert by_name["Maignan"].role_mantra_labels == ["Portiere"]


class TestTheColumns:
    def test_the_stat_keys_are_the_pages_columns_in_the_pages_order(
        self, rows: list[PlayerStatsRow]
    ) -> None:
        """What this catches is a key **renamed or dropped** — not a mapping that slipped.

        ⚠ It was written claiming the second, and the claim was measured false on
        2026-09-24. Shifting every `td data-col-key` one position along `STAT_COL_KEYS`
        in the fixture leaves this test **green**, and structurally so: the page renders
        exactly the eleven keys of `STAT_COL_KEYS` in exactly that order, so a cyclic
        shift maps column *i* onto key *i+1* and the surviving dict has byte-identical
        insertion order. Only the values rotate. Ten other tests in this file go red on
        it, and the one written for it is
        `test_each_key_is_bound_to_the_kind_of_value_its_column_renders`.

        What it does carry is the rename: `ass` renamed upstream drops the key from all
        679 rows, `orders` stops being `{STAT_KEYS}`, and this is one of six tests that
        say so.
        """
        orders = {tuple(row.stats) for row in rows}

        assert orders == {STAT_KEYS}
        assert tuple(rows[0].stats) == (
            "pg", "mv", "mfv", "gol", "gs", "rig", "rp", "ass", "amm", "esp",
        )

    def test_each_key_is_bound_to_the_kind_of_value_its_column_renders(
        self, rows: list[PlayerStatsRow]
    ) -> None:
        """The binding, which the key order above does not carry.

        `run()` reads `stats["mv"]` by name, so a value under the wrong key is not a
        crash — it is a wrong number written to `media_voto` and upserted. Two measured
        page changes corrupt values while every row still parses and the key set stays
        exactly right, and this is what notices both:

        * the `data-col-key` mapping slipping one position — `pg` holds `'ATA'`, `mv`
          holds `'36'`, `rig` holds `'0'`;
        * a stat cell growing a second figure, `<td data-col-key="pg">31
          <small>(310')</small></td>` — `handle_data` accumulates, so `pg` becomes
          `"31 (310')"`. That one is loud in production (`counter()`'s `int()` raises for
          the whole run) and was silent here, because only five rows are pinned by value
          and it is realistic on the other 674.

        The shapes are the page's own, measured, not invented: see `COLUMN_SHAPES`.
        """
        wrong = [
            (row.name, key, value)
            for row in rows
            for key, value in row.stats.items()
            if not re.fullmatch(COLUMN_SHAPES[key], value)
        ]

        assert wrong == [], f"cells that are not their column's kind: {wrong[:5]}"

    def test_a_column_inserted_upstream_is_dropped_and_shifts_nothing(self) -> None:
        """The danger this file was first written to look for, and it is not one.

        Every value travels with its own `data-col-key`, so an inserted column is not
        captured rather than displacing the ones after it — the opposite of a
        positional parser. Measured on the fixture too (679 inserted cells, whole file
        green), but asked here of the parser directly: on the page the assertion would
        be vacuous, because the page has no such column to drop.
        """
        extra = '<td class="player-xg" data-col-key="xg">\n  1,23\n</td>\n'
        row = _one_row("<span>Retegui</span>", cells=extra + CELLS)

        assert "xg" not in row.stats
        assert tuple(row.stats) == STAT_KEYS
        assert (row.stats["pg"], row.stats["mv"]) == ("36", "6,47")
        assert row.team == "ATA"

    def test_a_specific_value_belongs_to_a_specific_key(
        self, by_name: dict[str, PlayerStatsRow]
    ) -> None:
        """The key set can be right while every value under it is its neighbour's."""
        retegui = by_name["Retegui"]

        assert retegui.stats["pg"] == "36"
        assert retegui.stats["gol"] == "25"
        assert retegui.stats["ass"] == "7"
        assert retegui.stats["amm"] == "2"
        assert retegui.stats["mfv"] == "8,64"

    def test_the_team_column_is_a_field_and_not_a_stat(
        self, by_name: dict[str, PlayerStatsRow]
    ) -> None:
        """`sq` is in `STAT_COL_KEYS` — it has to be, or the cell is never captured — but
        it is routed to `team`. If it ever lands in `stats` instead, `squadra` is upserted
        as `""` and the dict `run()` iterates has an eleventh key nothing reads."""
        retegui = by_name["Retegui"]

        assert retegui.team == "ATA"
        assert "sq" not in retegui.stats
        assert len(retegui.stats) == 10

    def test_the_compound_rigori_cell_keeps_both_numbers(
        self, by_name: dict[str, PlayerStatsRow]
    ) -> None:
        """One column, two numbers: `segnati / tirati`. Retegui's two differ (4 of 5), so
        this row says which half is which; `0 / 0` could not. The exact rendering is
        pinned because `_rig_part` splits on the literal `/` and strips each half — a
        parser that collapsed the spaces or the slash would change what `run()` counts."""
        retegui = by_name["Retegui"]

        assert retegui.stats["rig"] == "4 / 5"
        assert retegui.rigori_segnati == "4"
        assert retegui.rigori_tirati == "5"
        assert by_name["Venturino"].stats["rig"] == "0 / 0"
        assert by_name["Venturino"].rigori_segnati == "0"

    def test_a_cell_with_no_slash_yields_neither_half(self) -> None:
        """`_rig_part` returns `""` rather than guessing, and `counter("")` reads it as 0.
        The page has no such cell today; this pins the behaviour if it grows one."""
        row = PlayerStatsRow(season="2024/25", provider="italia", stats={"rig": "4"})

        assert (row.rigori_segnati, row.rigori_tirati) == ("", "")

    def test_the_averages_stay_italian_decimals(
        self, by_name: dict[str, PlayerStatsRow]
    ) -> None:
        """The parser hands `run()` the page's own text and does no arithmetic. That is
        the contract `italian_decimal` is written against: it *raises* on a dot, because a
        dot means the wrong column is being read, and it would never see one if the parser
        had already converted. A parser that stripped the comma as a thousands separator
        would turn 6,47 into 647 and nothing downstream would object."""
        retegui = by_name["Retegui"]

        assert isinstance(retegui.stats["mv"], str)
        assert (retegui.stats["mv"], retegui.stats["mfv"]) == ("6,47", "8,64")
        assert italian_decimal(retegui.stats["mv"]) == Decimal("6.47")
        assert italian_decimal(retegui.stats["mfv"]) == Decimal("8.64")

    def test_the_no_data_marker_is_passed_through_and_not_interpreted(
        self, rows: list[PlayerStatsRow], by_name: dict[str, PlayerStatsRow]
    ) -> None:
        """119 of the 679 never played, and the page renders their averages as `0,0`.
        The parser leaves it alone; collapsing `0,0` to `None` is `italian_decimal`'s job
        and doing it twice, in two places, is how the two come to disagree."""
        never_played = [row for row in rows if row.stats["mv"] == "0,0"]

        assert len(never_played) == 119  # == the database's 119 NULL `media_voto`
        assert by_name["Scamacca"].stats["mv"] == "0,0"
        assert by_name["Scamacca"].stats["pg"] == "0"
        assert italian_decimal(by_name["Scamacca"].stats["mv"]) is None

    def test_the_cells_are_stripped_of_the_pages_indentation(
        self, by_name: dict[str, PlayerStatsRow]
    ) -> None:
        """Every cell is rendered as `\\n<28 spaces>36\\n<24 spaces>`. `counter` calls
        `int()` on what arrives, so unstripped text is a `ValueError` for the whole run."""
        retegui = by_name["Retegui"]

        assert retegui.stats["pg"] == "36"
        assert retegui.name == "Retegui"
        assert retegui.team == "ATA"
        assert all(v == v.strip() for v in retegui.stats.values())


class TestTheTeamCell:
    """`team` is overwritten the same way `name` is, and the same page change breaks it.

    Found on 2026-09-24 by a reviewer who noticed that closing the `name` gap left its twin
    three lines away untouched. `handle_data` has three branches — `name` overwrite, `team`
    overwrite, stats accumulate — and pinning one overwrite says nothing about the other.

    The realistic trigger is the one the site already renders elsewhere: a club cell that
    gains a second text node. A loan or transfer badge beside the code (`ATA` + `PRE`), a
    logo's alt text, or the full club name before the abbreviation each leave `team` holding
    the **last** node rather than the code, and every downstream join is on that code.
    """

    def test_every_team_is_a_three_letter_code_and_not_a_trailing_fragment(
        self, rows: list[PlayerStatsRow]
    ) -> None:
        """Pins the shape, so a second text node in the `sq` cell cannot pass as a club.

        Measured on this fixture: 20 distinct codes, every one exactly three characters and
        upper-case. A trailing badge would leave a value that is neither.
        """
        teams = {r.team for r in rows}

        assert len(teams) == 20, sorted(teams)
        assert all(len(t) == 3 and t.isupper() for t in teams), sorted(teams)

    def test_a_club_cell_with_two_text_nodes_keeps_only_the_last(
        self, rows: list[PlayerStatsRow]
    ) -> None:
        """The current behaviour, pinned rather than corrected.

        Synthetic, because the fixture's cells each hold one node — which is exactly why the
        defect is invisible on real bytes. Driving the parser directly is the only way to
        state what it does today, so a change in either direction is a decision somebody
        makes rather than one that happens. `name` is pinned the same way above.
        """
        parser = StatisticheParser("2024/25", "italia")
        parser.feed(
            '<tr class="player-row">'
            '<td data-col-key="sq">ATA<span class="badge">PRE</span></td>'
            "</tr>"
        )

        # `</tr>` is what appends the row, so read the public surface rather than `_row`,
        # which the parser has already cleared by the time `feed` returns.
        assert [r.team for r in parser.rows] == ["PRE"]


class TestTheNameCell:
    """`name` is **one of two** fields `handle_data` overwrites instead of accumulating.

    ⚠ This docstring said "the one field" until 2026-09-24 and that was wrong: `team` is
    the identical branch three lines below it (`statistiche.py:187-188`,
    `elif self._capture_key == "sq": self._row.team = text`), and only the stats branch
    accumulates. `TestTheTeamCell` below pins the other half; neither should be read as
    covering both.

    The site already wraps the name in a `<span>` inside `<a class="player-name">`, so
    there is exactly one text node and the difference does not show. A second element
    inside that link — a surname span, a status badge — and only the last node survives.
    """

    def test_every_name_is_the_whole_text_of_its_profile_link(
        self, rows: list[PlayerStatsRow]
    ) -> None:
        """The page change, caught by reading the link a second way.

        `NAME_LINK` keeps every text node; the parser keeps one. They agree on all 679
        names today and stop agreeing the moment the site splits one. Two realistic
        forms were measured against the fixture, both invisible to every other test in
        this file:

        * `<span>Kolo </span><span>Muani</span>`, a surname or prefix span — the page
          already renders `Zapata D.`, `Martinez L.`, `Sulemana I.`, so splitting on the
          initial is exactly the shape it would take. **138 of 679 names** become their
          last fragment.
        * a status badge on injured players, `<span class="badge-status">INF</span>`
          appended inside the link. At a realistic 8% two of three seeds left the file
          entirely green, with **55-57 names silently becoming `'INF'`** — the third
          failed only because it happened to hit Maignan, who is pinned by name.

        This test is the only one that does not need the corruption to land on a pinned
        row. It is deliberately the *correct* contract rather than the current one: it
        goes green again if the parser is repaired to accumulate, and the two pins below
        are what make that repair a decision rather than a drift.
        """
        raw = gzip.decompress(PAGE.read_bytes()).decode("utf-8")
        links = NAME_LINK.findall(raw)

        assert len(links) == EXPECTED_ROWS
        assert [row.name for row in rows] == [_visible_text(inner) for inner in links]

    def test_a_name_split_across_two_elements_keeps_only_the_last_fragment(self) -> None:
        """Pins what the parser does now, which is lose the first half.

        ⚠ This is a hazard recorded, not a contract endorsed. `handle_data` assigns
        `self._row.name = text` where the stats branch joins onto what is already
        there, and nothing in the file said which of the two was intended — so the
        defect and its obvious repair were equally unconstrained and either could have
        happened by accident. Repairing it turns this test red; that is the point.
        """
        assert _one_row("<span>Kolo </span><span>Muani</span>").name == "Muani"

    def test_a_badge_inside_the_name_link_replaces_the_name(self) -> None:
        """The same overwrite, in the form the site is likeliest to ship it.

        An availability badge is markup a fantacalcio page grows on injured players
        only, so it would corrupt a shifting minority of rows and leave the count, the
        ids, the teams and every stat untouched. The name it writes is not empty, so
        `all(row.name for row in rows)` still holds.
        """
        row = _one_row('<span>Retegui</span><span class="badge-status">INF</span>')

        assert row.name == "INF"
        assert row.player_id == "6228"
        assert row.stats["gol"] == "25"

    def test_the_walk_up_the_href_stops_at_the_last_digits_and_not_the_first(self) -> None:
        """`reversed(href.split("/"))` — the walk runs from the end, and today it cannot
        be seen to.

        The href carries exactly one all-digit segment (`/retegui/6228/2024-25/italia`;
        the season keeps its dash), so forward and backward find the same one and the
        direction is unobservable on the page. It stops being unobservable if the site
        ever drops that dash, and then the **last** digit segment is the season: the
        parser would upsert `player_id = 2025` for every row on the page, against a
        `players` foreign key, with nothing raising.

        ⚠ `2025` below is the wrong player and is pinned anyway, so that changing the
        direction is a decision. The page change itself is already caught —
        `test_the_id_comes_from_the_profile_link` and `test_reteguis_whole_row` both go
        red on it — and what this adds is the reason the message will say `2025`.
        """
        href = "https://www.fantacalcio.it/serie-a/squadre/atalanta/retegui/6228/2025/italia"

        assert _one_row("<span>Retegui</span>", href=href).player_id == "2025"


class TestTheProviderLabel:
    def test_the_provider_is_carried_onto_every_row(
        self, rows: list[PlayerStatsRow]
    ) -> None:
        """`fonte` is half the upsert's conflict key. The same player has one row per
        provider with different numbers, so a row that loses its label collapses three
        ratings into one — and the collapse is an `ON CONFLICT DO UPDATE`, so two of the
        three are overwritten rather than rejected."""
        assert {row.provider for row in rows} == {"italia"}
        assert {row.season for row in rows} == {"2024/25"}

    def test_the_label_is_the_parsers_and_is_not_read_off_the_page(self) -> None:
        """Nothing on the page names the provider — the URL does. So the same bytes parse
        identically under another label, which is exactly why `run()` must fetch once per
        provider rather than reuse one page three times."""
        as_statistico = _parse("statistico")

        assert {row.provider for row in as_statistico} == {"statistico"}
        assert len(as_statistico) == EXPECTED_ROWS
        assert next(r for r in as_statistico if r.name == "Retegui").stats["mv"] == "6,47"


class TestTheAddressesRunReads:
    def test_the_season_is_dashed_into_the_path(self) -> None:
        """`2024/25` in the database, `2024-25` in the URL. A slash left in would make a
        third path segment and the site would answer 404."""
        assert (
            stats_url("2024/25", "italia")
            == "https://www.fantacalcio.it/statistiche-serie-a/2024-25/italia"
        )
        assert stats_url("2026/27", "fantacalcio").endswith("/2026-27/fantacalcio")

    def test_run_iterates_the_three_providers_the_site_publishes(self) -> None:
        """One fetch each, because the three are real server-side datasets that disagree
        — the module docstring measures Immobile at 6,22 / 6,17 / 6,16 across them."""
        assert PROVIDERS == ["fantacalcio", "statistico", "italia"]
        assert [stats_url("2024/25", p).rsplit("/", 1)[1] for p in PROVIDERS] == PROVIDERS
