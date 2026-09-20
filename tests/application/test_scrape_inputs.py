"""T23: what `db scrape` may be asked for — the refusals, and the stale season default.

The lift's own proof. `db_scrape`'s Typer body held three things the app cannot reach:
which tables are scrapable, which module each one is, and what happens when no
`--season` is given. It validated nothing else at all, so `--season 2022/26` built
`.../2022-26/1`, 404'd, retried three times with backoff and *then* failed — per
giornata, for 38 giornate.

The fourth thing is the one this task exists for. `voti.DEFAULT_SEASONS` and
`statistiche.DEFAULT_SEASONS` stopped at 2025/26 while the season being played was
2026/27, so a run that omitted `--season` scraped last season and reported success.
**Both now reach 2026/27** and the assertions below say so; what is kept is the
*report*, because the next August puts them behind again. `scrapables` reads each
scraper's **own** list rather than keeping a copy, which is why a test here can flip the
staleness by moving the scraper's list and not this module's.

No socket: nothing below calls `run`, and the one test that does patches it.
"""

from __future__ import annotations

from datetime import date

import pytest

from fantabot.application.scrape import (
    InvalidScrape,
    clean_scrape,
    current_season,
    run_scrape,
    scrapables,
)


def table(name: str, *, current: str = "2026/27"):  # type: ignore[no-untyped-def]
    """The one `Scrapable` a test is about, by name."""
    found = {s.table: s for s in scrapables(current)}
    return found[name]


# -- the refusals ----------------------------------------------------------------------


def test_an_unknown_table_is_refused_and_the_three_are_named() -> None:
    with pytest.raises(InvalidScrape) as refused:
        clean_scrape("fixtures", [])
    message = str(refused.value)
    assert "fixtures" in message
    for known in ("quotazioni", "statistiche", "voti"):
        assert known in message


# `2026/27x` and `x2026/27` are the anchor cases: an unanchored pattern finds a real
# season inside each of them, and the consecutive-years check then passes it.
@pytest.mark.parametrize(
    "season",
    ["2022-23", "22/23", "2022/2023", "", "   ", "next", "2026/27x", "x2026/27"],
)
def test_a_season_that_is_not_yyyy_slash_yy_is_refused(season: str) -> None:
    with pytest.raises(InvalidScrape):
        clean_scrape("voti", [season])


def test_a_season_whose_halves_are_not_consecutive_is_refused() -> None:
    """`2022/26` is well-formed and has no page. The site answers that in ~30 s of retries."""
    with pytest.raises(InvalidScrape) as refused:
        clean_scrape("voti", ["2022/26"])
    assert "2022/26" in str(refused.value)


def test_the_century_rollover_is_consecutive() -> None:
    """`2099/00` is the one pair where `+1` is not `end`. Refusing it would be arithmetic."""
    assert clean_scrape("voti", ["2099/00"]).seasons == ("2099/00",)


def test_surrounding_whitespace_is_not_a_refusal() -> None:
    assert clean_scrape("voti", [" 2026/27 "]).seasons == ("2026/27",)


def test_the_table_comes_back_normalised() -> None:
    """Which is why a caller spawns `request.table` and never the string it was handed."""
    assert clean_scrape(" VOTI ", ["2026/27"]).table == "voti"


def test_an_unknown_table_is_quoted_back_as_it_was_typed() -> None:
    """The operator is the only person who can match the message against what they typed."""
    with pytest.raises(InvalidScrape) as refused:
        clean_scrape(" Fixtures ", [])
    assert "' Fixtures '" in str(refused.value)


def test_a_repeated_season_is_asked_for_once_in_the_order_given() -> None:
    """A scrape is minutes per season and an upsert. Twice costs the time and changes nothing."""
    request = clean_scrape("voti", ["2026/27", "2025/26", "2026/27"])
    assert request.seasons == ("2026/27", "2025/26")


# -- what "no --season" means ----------------------------------------------------------


def test_no_season_resolves_to_that_scrapers_own_default() -> None:
    """Resolved here, not left as an empty tuple: the job log has to say what ran."""
    from fantabot.adapters.scraping import voti

    assert clean_scrape("voti", []).seasons == tuple(voti.DEFAULT_SEASONS)


def test_every_table_resolves_to_the_same_span() -> None:
    """This asserted the *opposite* until the defaults were fixed.

    Its point was the trap, stated as a comparison: `quotazioni` carried 2026/27 and the
    other two did not, so `clean_scrape("quotazioni", [])` and `clean_scrape("voti", [])`
    disagreed. They no longer do, and the same comparison is now how a *new* divergence
    shows up — one scraper's list moving on without the others is the shape the original
    defect had.
    """
    from fantabot.adapters.scraping import statistiche, voti

    spans = {name: clean_scrape(name, []).seasons for name in ("quotazioni", "voti", "statistiche")}

    assert len(set(spans.values())) == 1, f"the three scrapers disagree about the span: {spans}"
    assert spans["voti"] == tuple(voti.DEFAULT_SEASONS)
    assert spans["statistiche"] == tuple(statistiche.DEFAULT_SEASONS)


# -- the stale default, carried where a screen can read it -----------------------------


def test_no_table_defaults_to_a_season_that_is_over() -> None:
    """Was `test_voti_and_statistiche_default_to_a_season_that_is_over`, asserting `True`.

    The report was built because the two defaults were behind; the report is kept because
    the *next* August will put them behind again, and the assertion moves to the state the
    fix produced rather than being deleted with the defect.
    """
    assert table("voti").default_is_stale is False
    assert table("statistiche").default_is_stale is False
    assert table("quotazioni").default_is_stale is False


def test_staleness_is_read_from_the_scraper_and_never_copied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Move the scraper's own list and the flag follows, with no edit here.

    A copy in this module would be a second default to keep in step, which is the defect
    rather than the report of it.

    **The direction is inverted from how this was first written, and it had to be.** It
    used to *append* 2026/27 to a list that stopped at 2025/26 and assert the flag went
    False. Once the real defaults reached 2026/27 that append became a no-op — the list
    was already complete — so the test passed whatever `default_is_stale` computed, and
    the one detector meant to catch the next August was left proving nothing. Shortening
    the list is the version that cannot go vacuous: it fails the day the flag stops
    reading the scraper.
    """
    from fantabot.adapters.scraping import voti

    assert table("voti").default_is_stale is False, "precondition: the real list is current"

    monkeypatch.setattr(voti, "DEFAULT_SEASONS", list(voti.DEFAULT_SEASONS[:-1]))

    assert table("voti").default_is_stale is True
    assert table("voti").default_seasons[-1] == "2025/26"


def test_staleness_is_measured_against_the_season_it_is_asked_about() -> None:
    """The comparison is a parameter, so nothing here has to be edited each August."""
    assert table("voti", current="2025/26").default_is_stale is False


# -- the order a fresh database needs --------------------------------------------------


def test_quotazioni_is_first_and_needs_nothing() -> None:
    """`players` and `teams` have no outbound keys; everything else points at them."""
    listed = scrapables("2026/27")
    assert listed[0].table == "quotazioni"
    assert listed[0].requires == ()


def test_the_other_two_say_they_need_quotazioni() -> None:
    assert table("statistiche").requires == ("quotazioni",)
    assert table("voti").requires == ("quotazioni",)


def test_each_table_says_which_rows_it_writes() -> None:
    assert table("quotazioni").writes == ("quotazioni", "players", "teams")
    assert table("voti").writes == ("voti", "bonus_malus")
    assert table("statistiche").writes == ("statistiche",)


# -- the derivation of "now" -----------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 9, 20), "2026/27"),
        (date(2026, 7, 1), "2026/27"),
        (date(2026, 6, 30), "2025/26"),
        (date(2027, 1, 15), "2026/27"),
        (date(2099, 8, 1), "2099/00"),
    ],
)
def test_the_season_being_played_is_derived_not_pinned(today: date, expected: str) -> None:
    """A constant to edit every August is the same disease as the default it detects."""
    assert current_season(today) == expected


# -- the run ---------------------------------------------------------------------------


def test_the_run_calls_that_tables_module_with_the_resolved_seasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fantabot.adapters.scraping import statistiche, voti

    called: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(voti, "run", lambda seasons: called.append(("voti", list(seasons))))
    monkeypatch.setattr(
        statistiche, "run", lambda seasons: called.append(("statistiche", list(seasons)))
    )

    run_scrape(clean_scrape("voti", ["2026/27", "2025/26"]))

    assert called == [("voti", ["2026/27", "2025/26"])]
