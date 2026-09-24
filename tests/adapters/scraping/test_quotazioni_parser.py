"""The listone, read from a recorded page — the parser the asta prices against.

`quotazioni` is the season's player list: it is the only scraper that writes `players` and
`teams`, and its `fvm` is what a plan is built out of. CLAUDE.md records what a silent hole
here costs — a Classic run whose prices never loaded bought a 25-man roster for 25 credits
of 500, and nothing raised, because an empty mapping is legal. The module answers a changed
page with `SystemExit(1)`, and until now nothing checked that it would notice.

Everything below is measured against `tests/fixtures/scraping/quotazioni-2024-25.html.gz`,
a real page from a completed season, cross-checked when it was recorded against the 679
players the database holds for 2024/25. The parser is driven directly with `.feed(html)`.

`TestTheListoneFetchRetries` is the one section that does go through `fetch_season`, and
it does so on purpose: its subject is the *fetch*, not the parse. It still opens no
socket — `urlopen` and `time.sleep` are injected, and `tests/conftest.py` would fail the
node if they were not.

The page changes this file is built to survive all have one shape: **the row count stays
right and the values stop meaning what they say**. A structural collapse is loud — zero
rows, and `run()` exits 1. A column that keeps its place and changes its content is silent,
and every corpus-level count below exists because a per-player assertion cannot see it.
"""

from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable

import pytest
from _paths import FIXTURES

from fantabot.adapters.scraping._site import BACKOFF_STEP_SECONDS, MAX_RETRIES
from fantabot.adapters.scraping.quotazioni import (
    PlayerRow,
    QuotazioniParser,
    fetch_season,
    season_url,
)

FIXTURE = FIXTURES / "scraping" / "quotazioni-2024-25.html.gz"

#: The 2024/25 Serie A, as three-letter codes. `quotazioni` is where a promoted club enters
#: the database at all, so a missing one is not a cosmetic difference.
CLUBS = {
    "ATA", "BOL", "CAG", "COM", "EMP", "FIO", "GEN", "INT", "JUV", "LAZ",
    "LEC", "MIL", "MON", "NAP", "PAR", "ROM", "TOR", "UDI", "VEN", "VER",
}


@pytest.fixture(scope="module")
def listone() -> list[PlayerRow]:
    html = gzip.decompress(FIXTURE.read_bytes()).decode("utf-8")
    parser = QuotazioniParser("2024/25")
    parser.feed(html)
    return parser.rows


def _player(rows: list[PlayerRow], name: str) -> PlayerRow:
    matches = [r for r in rows if r.name == name]
    assert len(matches) == 1, f"{name}: expected exactly one row, got {len(matches)}"
    return matches[0]


class TestTheWholeListone:
    def test_the_page_yields_one_row_per_player(self, listone: list[PlayerRow]) -> None:
        """679, the number the database holds for 2024/25.

        Both directions matter. Too few is the class of failure the module already names —
        a renamed row class and the season silently shrinks. Too many is the quieter one:
        the page holds 701 `<tr>` elements, 22 of them header and spacer rows, and a loop
        that stopped keying on `player-row` would enrol every one of them as a player.
        """
        assert len(listone) == 679

    def test_every_row_carries_an_id_a_name_and_a_team(self, listone: list[PlayerRow]) -> None:
        """No cleanliness is being asserted that the page does not have: measured on this
        fixture, all 679 rows are complete on all three, and the 679 ids are distinct.
        `player_id` is the join key the rest of the system uses, and `run` calls `int()` on
        it unguarded — an empty one is a `ValueError` that takes the whole season down."""
        assert [r for r in listone if not r.player_id] == []
        assert [r for r in listone if not r.name] == []
        assert [r for r in listone if not r.team] == []
        assert all(r.player_id.isdigit() for r in listone)
        assert len({r.player_id for r in listone}) == 679

    def test_the_679_names_are_679_distinct_names(self, listone: list[PlayerRow]) -> None:
        """Non-empty is not the assertion that matters about a name; *distinct* is.

        `name` goes straight into `players.nome`, and neither the row count nor the
        emptiness check above can see it change. Measured: adding one badge node inside
        the player link — `<span>Retegui</span><span class="badge">NEW</span>`, the shape
        the site already uses for flags elsewhere on the row — renames the row, because
        `handle_data` assigns `name` on every text node inside the `<a>` and the last one
        wins. Applied to 676 of the 679 rows, sparing only the three this file names by
        hand so `_player()` keeps resolving, the entire suite stayed green with 676
        players called "NEW". This count is what sees it: 679 distinct names collapse
        to 4.

        Distinctness is a property of the page, not an assumption imposed on it — the
        site disambiguates shared surnames itself, and this listone has no repeat.
        """
        assert len({r.name for r in listone}) == 679

    def test_the_appearance_count_is_read_per_row_and_not_defaulted(
        self, listone: list[PlayerRow]
    ) -> None:
        """`played_last_season` is `data-filter-playeds`, defaulting to "0" when absent —
        so the attribute going away is invisible from inside any single row, and "0" is
        also a legitimate reading for the 118 players who did not appear.

        Measured: dropping the attribute from 678 of the 679 rows, keeping only Retegui's
        (the one row pinned field-by-field below), left the whole suite green with 678
        players reading "0". The corpus count is the only witness.

        Nothing consumes this field today — `run()` never puts it in the payload — so
        what fails here is a drift detector rather than a guard on a value. It is kept
        because it is the only per-appearance signal the page carries, and CLAUDE.md's
        still-open "Stats source" is what would reach for it.
        """
        assert sum(1 for r in listone if r.played_last_season != "0") == 561
        assert all(r.played_last_season.isdigit() for r in listone)

    def test_every_price_is_a_number_and_not_merely_present(
        self, listone: list[PlayerRow]
    ) -> None:
        """`run()` calls `int(r.c_fvm or 0)` on all six, so what it needs from a price cell
        is not that it is non-empty but that it is a digit string.

        The counts one class down test *column identity* — that classic and mantra are not
        one column read twice — and identity survives corruption. Measured: rendering the
        movement arrow the site already draws beside a quotazione that moved,
        `<td data-col-key="c_qa">40<span class="trend">&uarr;</span></td>`, into the rows
        where the two listoni already price alike leaves all three of those counts exactly
        where they were, leaves nothing empty, leaves 679 rows, and leaves the suite green
        — with 636 of those rows carrying a `qa` of "↑" in both listoni. `int("↑")` then
        dies inside the upsert, a long way from the page that caused it; the signed
        variant the site also renders does not die at all, because `int("+3")` is 3.
        """
        for field in ("c_qi", "c_qa", "c_fvm", "m_qi", "m_qa", "m_fvm"):
            bad = [(r.name, getattr(r, field)) for r in listone if not getattr(r, field).isdigit()]
            assert bad == [], f"{field}: {len(bad)} not numeric, e.g. {bad[:3]}"

    def test_the_club_is_the_three_letter_code_this_page_uses(
        self, listone: list[PlayerRow]
    ) -> None:
        """Pinned here because the sibling `voti` fixture says `Verona` for the same player:
        two pages, two conventions, and the difference is real rather than a parse bug."""
        assert {r.team for r in listone} == CLUBS
        assert _player(listone, "Retegui").team == "ATA"


class TestTheHighestScoringRowOnRecord:
    def test_retegui_is_read_whole(self, listone: list[PlayerRow]) -> None:
        """Every field of one row, against one player who will keep meaning this: 2024/25's
        corpus-maximum fantavoto. Field-by-field rather than spot checks, because the ways
        this parser goes wrong are a column shifting and whitespace surviving a cell, and
        both show up as a *neighbouring* field being right."""
        assert _player(listone, "Retegui") == PlayerRow(
            season="2024/25",
            player_id="6228",
            name="Retegui",
            team="ATA",
            role_classic_code="a",
            role_mantra_codes=["pc"],
            played_last_season="95",
            c_qi="20",
            c_qa="40",
            c_fvm="500",
            m_qi="19",
            m_qa="40",
            m_fvm="500",
        )


class TestClassicAndMantraAreSeparateColumns:
    """The seam CLAUDE.md keeps a scar from: the two listoni share a row, share letters for
    their role codes, and differ in what they cost. A parser that reads one column for both
    is wrong in a way that still produces a full, plausible, priced listone."""

    def test_the_two_listoni_price_retegui_differently(self, listone: list[PlayerRow]) -> None:
        """Two tuples, and deliberately no third assertion that they differ.

        An `assert retegui.c_qi != retegui.m_qi` stood here and could not fail: once the
        two tuples above hold it is `"20" != "19"`, entailed by its own neighbours. The
        claim it was trying to make is real, and it is made one test down, where it is
        counted over the corpus and can actually go red.
        """
        retegui = _player(listone, "Retegui")

        assert (retegui.c_qi, retegui.c_qa, retegui.c_fvm) == ("20", "40", "500")
        assert (retegui.m_qi, retegui.m_qa, retegui.m_fvm) == ("19", "40", "500")

    def test_they_disagree_across_the_corpus_and_not_only_on_one_player(
        self, listone: list[PlayerRow]
    ) -> None:
        """One player's quotazioni can coincide — Retegui's `qa` and `fvm` do. Counted over
        all 679 rows the two columns cannot be confused for each other, and a column read
        twice would drive every one of these to zero.

        Why the single-player version above cannot replace this one, measured rather than
        argued: make `m_qa` mirror `c_qa`, or make `m_fvm` mirror `c_fvm` for every row but
        Retegui's, and `test_retegui_is_read_whole` and
        `test_the_two_listoni_price_retegui_differently` both stay green — Retegui's `qa`
        and `fvm` are the same number in both listoni, so the whole-row assertion has
        nothing to compare. This test is the only killer for either. Only `qi` differs on
        his row, which is exactly one of the six price fields.
        """
        assert sum(1 for r in listone if r.c_qi != r.m_qi) == 201
        assert sum(1 for r in listone if r.c_qa != r.m_qa) == 41
        assert sum(1 for r in listone if r.c_fvm != r.m_fvm) == 196


class TestTheColumnsAreReadByKeyAndNotByPosition:
    """The obvious way a table parser breaks — the site inserts a column and every value
    shifts one to the left — cannot happen here, and that is worth writing down so nobody
    "simplifies" it back. `handle_starttag` keys on `data-col-key`; a cell without one is
    not a column the parser counts but a cell it ignores. Measured on the fixture:
    inserting an unkeyed
    `<td class="player-trend">+3</td>` into all 679 rows left all 679 parsed rows
    identical on every field.

    One line makes that true and nothing observed it — the `_capture_key` reset in
    `handle_endtag`. With it deleted, that same inserted cell rewrote `m_fvm` to "+3" in
    all 679 rows: a full listone of plausible, non-empty, wrong prices, which `run()` would
    upsert as `int("+3")` for every player in the season. The test below is the witness.

    Its twin — the `if key:` guard in `handle_starttag` that keeps the previous key across
    an unkeyed cell — is the redundant half of the pair this repo's own memory warns about,
    and is deliberately left unpinned. See the note on the test.
    """

    def test_an_unkeyed_cell_never_becomes_the_cell_before_it(self) -> None:
        """A "Trend" or "Diff" column, which the site renders on its other tables, arrives
        as a `<td>` with text and no `data-col-key`. Placed both between two priced cells
        and after the last one, because those are different code paths: the first lands on
        a key that a later cell overwrites anyway, the second on one nothing ever replaces.

        The row also carries no `data-filter-playeds`, on purpose: the `"0"` in the
        expected row below is the only place the parser's default for a missing appearance
        count is exercised, and putting the attribute back here silently un-pins it.

        This kills the reset-branch deletion. It does not kill deleting the `if key:`
        guard, and nothing can: with the reset in place the guard only ever chooses
        between `None` and `None`, and with the reset gone the guard's absence is the
        *safer* behaviour. One of the two is unobservable by construction whichever way
        round they are read, so this pins the one that carries the behaviour.
        """
        html = (
            '<tr class="player-row" data-filter-role-classic="a" data-filter-role-mantra="pc">'
            '<th class="player-name"><a class="player-name" '
            'href="https://www.fantacalcio.it/serie-a/squadre/atalanta/retegui/6228">'
            "<span>Retegui</span></a></th>"
            '<td class="player-team" data-col-key="sq">ATA</td>'
            '<td data-col-key="c_qi">20</td>'
            '<td data-col-key="c_qa">40</td>'
            '<td data-col-key="c_fvm">500</td>'
            '<td class="player-trend">+3</td>'
            '<td data-col-key="m_qi">19</td>'
            '<td data-col-key="m_qa">40</td>'
            '<td data-col-key="m_fvm">500</td>'
            '<td class="tail">-1</td>'
            "</tr>"
        )
        parser = QuotazioniParser("2026/27")
        parser.feed(html)

        (row,) = parser.rows
        assert row == PlayerRow(
            season="2026/27",
            player_id="6228",
            name="Retegui",
            team="ATA",
            role_classic_code="a",
            role_mantra_codes=["pc"],
            played_last_season="0",
            c_qi="20",
            c_qa="40",
            c_fvm="500",
            m_qi="19",
            m_qa="40",
            m_fvm="500",
        )


class TestPlayerRowIsAClassToken:
    """`"player-row" in classes.split()`, not `in classes`. This page is 701 `<tr>` to 679
    players, and the 22 that are not players are where a spacer with a class of its own
    would go. The site already names its elements by prefix — `player-role-classic`,
    `player-role-mantra`, `player-name player-link` — so `player-row-header` is the
    natural name for one, and a substring test enrols it as a player with no id, no name,
    no team and no price. `run()` then calls `int("")` on it and the season dies with a
    `ValueError` a long way from the cause.

    Neither half is observable on the recorded page, which carries exactly one `<tr>`
    class and no prefixed variant of it. Both halves are therefore written out here
    rather than left to the fixture."""

    def test_a_row_whose_class_merely_starts_with_player_row_is_not_a_player(self) -> None:
        html = (
            '<tr class="player-row-header"><th>Ruolo</th><th>Nome</th></tr>'
            '<tr class="player-row" data-filter-role-classic="a" data-filter-role-mantra="pc">'
            '<th class="player-name"><a class="player-name" '
            'href="https://www.fantacalcio.it/serie-a/squadre/atalanta/retegui/6228">'
            "<span>Retegui</span></a></th>"
            '<td class="player-team" data-col-key="sq">ATA</td>'
            "</tr>"
        )
        parser = QuotazioniParser("2026/27")
        parser.feed(html)

        assert [r.name for r in parser.rows] == ["Retegui"]

    def test_a_player_row_carrying_a_second_class_is_still_a_player(self) -> None:
        """The other direction, and the reason the fix is `.split()` rather than a
        stricter equality: a highlight or sticky-row class added beside `player-row` must
        not drop the player."""
        html = (
            '<tr class="player-row player-row-highlight" data-filter-role-classic="a" '
            'data-filter-role-mantra="pc">'
            '<th class="player-name"><a class="player-name" '
            'href="https://www.fantacalcio.it/serie-a/squadre/atalanta/retegui/6228">'
            "<span>Retegui</span></a></th>"
            '<td class="player-team" data-col-key="sq">ATA</td>'
            "</tr>"
        )
        parser = QuotazioniParser("2026/27")
        parser.feed(html)

        assert [r.name for r in parser.rows] == ["Retegui"]


class TestMantraRolesAreAList:
    """A Mantra player carries one to three roles and the bipartite matcher in
    `domain/asta/legality.py` matches against the whole set. Keeping only the first is the
    defect a single-role example cannot see, and it would not raise anywhere — it would
    quietly make 311 of 679 players narrower than they are."""

    def test_a_two_role_player_keeps_both(self, listone: list[PlayerRow]) -> None:
        assert _player(listone, "McTominay").role_mantra_codes == ["m", "c"]

    def test_a_three_role_player_keeps_all_three_in_order(self, listone: list[PlayerRow]) -> None:
        assert _player(listone, "Di Lorenzo").role_mantra_codes == ["b", "dd", "e"]

    def test_the_corpus_is_mostly_not_single_role(self, listone: list[PlayerRow]) -> None:
        assert Counter(len(r.role_mantra_codes) for r in listone) == {1: 368, 2: 269, 3: 42}

    def test_the_same_letter_means_different_things_in_the_two_systems(
        self, listone: list[PlayerRow]
    ) -> None:
        """`c` is Centrocampista in Classic and Cen.centrale in Mantra. The two code scales
        overlap numerically and by letter, and CLAUDE.md records a live instance of reading
        one through the other's map — a real code, the wrong one, and nothing raised."""
        mctominay = _player(listone, "McTominay")

        assert mctominay.role_classic_code == "c"
        assert "c" in mctominay.role_mantra_codes
        assert mctominay.role_classic_label == "Centrocampista"
        assert mctominay.role_mantra_labels == ["Mediano", "Cen.centrale"]


class TestThePlayerLinkCarriesTheId:
    """The fixture is a past season, whose links end `/<id>/2024-25`. The weekly run reads
    the *current* season, whose links end `/<id>` — a shape no fixture here covers, and the
    one where "just take the segment before the season" silently stops finding an id.

    What resolves all three below is the *reversed scan for the first all-digit segment*,
    and only that. The trailing-slash case is not the `.rstrip("/")` on the href doing the
    work: `"…/6228/".split("/")` reversed yields `""` first, which is not a digit, and the
    scan walks past it to `6228`. Deleting the `rstrip` changes nothing on any href, with
    or without a slash — it is unobservable by construction, and is left unpinned on
    purpose rather than covered by a test whose name would claim otherwise."""

    @pytest.mark.parametrize(
        "href",
        [
            "https://www.fantacalcio.it/serie-a/squadre/atalanta/retegui/6228/2024-25",
            "https://www.fantacalcio.it/serie-a/squadre/atalanta/retegui/6228",
            "https://www.fantacalcio.it/serie-a/squadre/atalanta/retegui/6228/",
        ],
    )
    def test_both_link_shapes_resolve_to_the_same_id(self, href: str) -> None:
        html = (
            '<tr class="player-row" data-filter-role-classic="a" data-filter-role-mantra="pc">'
            f'<th class="player-name"><a class="player-name" href="{href}">'
            "<span>Retegui</span></a></th>"
            '<td class="player-team" data-col-key="sq">ATA</td>'
            "</tr>"
        )
        parser = QuotazioniParser("2026/27")
        parser.feed(html)

        assert [(r.player_id, r.name, r.team) for r in parser.rows] == [("6228", "Retegui", "ATA")]


class TestTheSeasonUrl:
    def test_the_slash_becomes_a_hyphen(self) -> None:
        assert season_url("2024/25") == "https://www.fantacalcio.it/quotazioni-fantacalcio/2024-25"


#: One player row, enough for `fetch_season` to prove it parsed what the transport served
#: without dragging the 679-row fixture through a retry test.
ONE_ROW_PAGE = (
    '<tr class="player-row" data-filter-role-classic="a" data-filter-role-mantra="a|pc">'
    '<th class="player-name"><a class="player-name" '
    'href="https://www.fantacalcio.it/serie-a/squadre/atalanta/retegui/6228">'
    "<span>Retegui</span></a></th>"
    '<td class="player-team" data-col-key="sq">ATA</td>'
    "</tr>"
)


class _Served:
    """What a fake `urlopen` hands back: a context manager whose `read()` is the body."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Served:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _Transport:
    """The site, scripted. `outcomes[i]` is what attempt *i+1* meets — an exception to
    raise or a page to serve — and the last entry repeats for any further attempt, so a
    test that means "always refused" writes the refusal once.

    A plain class and not a `@dataclass`: `dataclasses.field` would shadow the loop
    variable `field` that `TestTheWholeListone` already uses, which ruff catches as
    F402, and renaming a variable in the regression net to import a decorator is the
    wrong way round."""

    def __init__(self, outcomes: list[BaseException | str]) -> None:
        self.outcomes = outcomes
        self.requests: list[urllib.request.Request] = []
        self.sleeps: list[float] = []

    def urlopen(self, req: urllib.request.Request, timeout: float | None = None) -> _Served:
        self.requests.append(req)
        outcome = self.outcomes[min(len(self.requests), len(self.outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return _Served(outcome.encode("utf-8"))

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Transport]:
    """Inject the transport and the clock, and record both.

    `time.sleep` is patched as well as `urlopen`, and not only for speed: a retry test
    that really slept would spend 6 seconds proving the backoff, and the *values* slept
    are themselves part of what is pinned below. `monkeypatch` restores both, so a test
    that raises cannot leave a scripted site behind it.
    """

    def install(*outcomes: BaseException | str) -> _Transport:
        scripted = _Transport(list(outcomes))
        monkeypatch.setattr(urllib.request, "urlopen", scripted.urlopen)
        monkeypatch.setattr(time, "sleep", scripted.sleep)
        return scripted

    return install


class TestTheListoneFetchRetries:
    """`quotazioni` fetches through `_site.fetch_html` — three attempts, 2 s then 4 s.

    **This is a behaviour change made on 2026-09-24, not a property the module always
    had.** Until then the listone was the one page fetched single-shot: measured before
    the change, a refused connection meant exactly one call to `urlopen`, no sleep, and
    `URLError` out of `fetch_season` — which escaped `run` uncaught, past the
    `SystemExit(1)` that is supposed to mean the page structure changed, and reached the
    operator as a traceback instead of a diagnosis.

    The asymmetry had an argument behind it, recorded in `_site.fetch_html`: one request
    per season against 38 for `voti`. Nobody measured it either way, and the owner's call
    was to make the three scrapers symmetric. So every count below is the thing to look at
    if that is ever reconsidered — they are the change, not scaffolding around it.
    """

    def test_a_transient_refusal_is_retried_and_the_season_still_lands(
        self, transport: Callable[..., _Transport]
    ) -> None:
        """The whole point. Before the change this raised on the first refusal."""
        scripted = transport(urllib.error.URLError("[Errno 54] reset by peer"), ONE_ROW_PAGE)

        rows = fetch_season("2024/25")

        assert [(r.player_id, r.name, r.team) for r in rows] == [("6228", "Retegui", "ATA")]
        assert len(scripted.requests) == 2

    def test_a_persistent_refusal_still_raises_the_last_error(
        self, transport: Callable[..., _Transport]
    ) -> None:
        """Retrying is not swallowing: a site that is genuinely down still fails the run,
        and it fails with the *last* attempt's error rather than the first. Which one
        surfaces is not cosmetic — the three can differ (a reset, then a refused
        connection, then a 503), and the one worth showing an operator is the state the
        site was left in."""
        first = urllib.error.URLError("[Errno 54] reset by peer")
        second = urllib.error.URLError("[Errno 61] connection refused")
        last = urllib.error.URLError("[Errno 60] operation timed out")
        scripted = transport(first, second, last)

        with pytest.raises(urllib.error.URLError) as caught:
            fetch_season("2024/25")

        assert caught.value is last
        assert len(scripted.requests) == 3

    def test_the_backoff_is_two_seconds_then_four(
        self, transport: Callable[..., _Transport]
    ) -> None:
        """`BACKOFF_STEP_SECONDS * attempt`, and no sleep after the final attempt.

        The trailing sleep is the half of this that a count of requests cannot see: a
        version that slept after the last failure would be correct in every other respect
        and would still cost the operator 6 seconds of silence before an error it was
        always going to get."""
        refused = urllib.error.URLError("[Errno 61] connection refused")
        scripted = transport(refused)

        with pytest.raises(urllib.error.URLError):
            fetch_season("2024/25")

        assert scripted.sleeps == [2, 4]

    def test_a_page_served_first_time_is_not_slept_on(
        self, transport: Callable[..., _Transport]
    ) -> None:
        """The common case, and the one the retry must not tax. `REQUEST_DELAY_SECONDS`
        is a separate, deliberate second between *seasons* and is `run`'s, not the
        fetch's — nothing here should sleep when the first GET answers."""
        scripted = transport(ONE_ROW_PAGE)

        assert [r.name for r in fetch_season("2024/25")] == ["Retegui"]
        assert (len(scripted.requests), scripted.sleeps) == (1, [])

    def test_a_timeout_is_retried_like_a_refusal(
        self, transport: Callable[..., _Transport]
    ) -> None:
        """`TimeoutError` is not a `URLError` and is caught beside it. A listone page is
        the biggest of the three the site serves, so the slow answer is the blip this
        scraper is likeliest to meet, and catching only `URLError` would retry everything
        except it."""
        scripted = transport(TimeoutError("timed out"), ONE_ROW_PAGE)

        assert [r.name for r in fetch_season("2024/25")] == ["Retegui"]
        assert len(scripted.requests) == 2

    def test_the_same_request_object_is_reused_across_attempts(
        self, transport: Callable[..., _Transport]
    ) -> None:
        """One `Request`, built once — which is also what carries the browser User-Agent
        the site needs to serve a table at all. A retry that rebuilt it would work today
        and would be the place a header quietly stops being sent."""
        refused = urllib.error.URLError("[Errno 61] connection refused")
        scripted = transport(refused, refused, ONE_ROW_PAGE)

        assert [r.name for r in fetch_season("2024/25")] == ["Retegui"]
        assert len({id(req) for req in scripted.requests}) == 1
        assert scripted.requests[0].full_url == season_url("2024/25")

    def test_three_attempts_and_a_two_second_step_are_the_numbers(self) -> None:
        """The literals above are `_site`'s constants, named here so that changing one
        fails on the constant rather than on six counts whose origin is no longer
        obvious."""
        assert (MAX_RETRIES, BACKOFF_STEP_SECONDS) == (3, 2)
