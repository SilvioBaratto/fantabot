"""T27: what the hourly refresh still owes, what it runs, and what it survives.

Three sources, each owed once per matchday: the voti the projection is fitted on, the lega
sync that says which round is calculated, and the news that carries availability. The
scheduling rule is one sentence — a success is spent, a failure is owed again — and it is
the whole reason the refresh is cheap: without the marker, eight GETs against a live site
every hour, for ever.

`AssertionError` and `KeyboardInterrupt` are the two things a containment boundary must not
swallow (SPEC A19(3)); everything else is a source that failed. And a failure records the
exception's **type name only**, because a message can carry the DSN and this line goes to a
log the operator pastes.

Nothing here opens a session, a socket or a clock: the sources are a Protocol, the marker
store is in memory, and `now` is a parameter.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from fantabot.application.lineup_refresh import (
    RefreshInputs,
    RefreshReport,
    run_refresh,
)
from fantabot.domain.lineup.refresh import (
    SOURCES,
    Marker,
    SourceOutcome,
    SourceRecord,
    due,
    voti_giornate,
    voti_succeeded,
)

NOW = datetime(2026, 9, 23, 7, 0, 0)
DAY = date(2026, 9, 23)

#: A kickoff that puts `NOW` inside A13's news window, which opens 6h before it and closes
#: 2h before. Derived from `NOW`'s own UTC reading rather than written as a literal: `NOW` is
#: naive, `news_window_cutoff` compares in UTC via `astimezone`, and a literal would put the
#: fixture inside the window only on a machine in the zone it was written on.
#: Four hours ahead leaves the window [NOW-2h, NOW+2h), so `NOW` sits in the middle of it.
KICKOFF = (NOW.astimezone(UTC) + timedelta(hours=4)).replace(tzinfo=None).isoformat()


def _inputs(**over: Any) -> RefreshInputs:
    base: dict[str, Any] = {
        "league_id": 4103937,
        "season": "2026/27",
        "cmday": 6,
        "day": DAY,
        "roster_ids": (1, 2, 3),
        "short_giornate": (),
        "previous_calculated": True,
        # The window open by default, so every test here keeps asking what it was written to
        # ask. The window itself is `TestTheNewsWindow`'s subject, and those override these.
        "mstr": KICKOFF,
        "status_mday": 6,
    }
    base.update(over)
    return RefreshInputs(**base)


class _Sources:
    """Records what it was asked, and answers with canned outcomes."""

    def __init__(self, **answers: SourceOutcome | BaseException) -> None:
        self.calls: list[str] = []
        self.giornate: tuple[int, ...] = ()
        self._answers = answers

    def _answer(self, name: str) -> SourceOutcome:
        self.calls.append(name)
        answer = self._answers.get(name, SourceOutcome(name, "ok"))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def voti(self, inputs: RefreshInputs, giornate: Sequence[int]) -> SourceOutcome:
        self.giornate = tuple(giornate)
        return self._answer("voti")

    def lega(self, inputs: RefreshInputs) -> SourceOutcome:
        return self._answer("lega")

    def news(self, inputs: RefreshInputs) -> SourceOutcome:
        return self._answer("news")


class _MemoryMarker:
    def __init__(self, marker: Marker | None = None, *, fails: bool = False) -> None:
        self.marker = marker or Marker({})
        self.writes = 0
        self._fails = fails

    def read(self) -> Marker:
        return self.marker

    def write(self, marker: Marker) -> None:
        if self._fails:
            raise OSError("read-only file system")
        self.writes += 1
        self.marker = marker


class _Recorder:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *objects: Any, **_kw: Any) -> None:
        self.lines.append(" ".join(str(o) for o in objects))


@pytest.fixture
def news_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Callable[[str | None, str], None]:
    """Both layers `_news_on` reads, under the test's control instead of the operator's.

    `_news_on` goes through `config.live_setting`, which answers from `os.environ` when the
    name is there and otherwise **re-reads `.env` from disk on every call** (`config.py`,
    and for the reason recorded there). A test that only touched `os.environ` therefore left
    the second layer pointed at the repository's own `.env` — audit finding 1.11: the case
    that deleted the variable and asserted the gate was off was reading
    `FANTABOT_LINEUP_NEWS=on` back out of that file, failed from the repository root, and
    passed from any cwd without one.

    So: chdir into an empty `tmp_path`, which is the `.env` `live_setting` will resolve
    (`Path(".env")`, relative to the working directory), and clear the injection registry,
    which is the only thing that makes an exported variable lose to the file. Both layers
    then say nothing until a test says something, and `setter(raw, source)` says it through
    exactly one of them.
    """
    import fantabot.config as config

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(config.LINEUP_NEWS_VAR, raising=False)
    monkeypatch.setattr(config, "_DOTENV_INJECTED", {})

    def setter(raw: str | None, source: str) -> None:
        if raw is None:
            return  # absent from both layers: the file is not written and the var is unset
        if source == "environment":
            monkeypatch.setenv(config.LINEUP_NEWS_VAR, raw)
        elif source == "dotenv":
            (tmp_path / ".env").write_text(f"{config.LINEUP_NEWS_VAR}={raw}\n", encoding="utf-8")
        else:  # pragma: no cover - a typo in a parametrize id must not read as "nothing set"
            raise ValueError(f"unknown setting source {source!r}")

    return setter


def _run(sources: _Sources, store: _MemoryMarker, **over: Any) -> RefreshReport:
    return run_refresh(
        over.pop("inputs", _inputs()),
        sources=sources,
        marker_store=store,
        reporter=_Recorder(),
        now=NOW,
        **over,
    )


class TestWhatIsDue:
    def test_everything_is_due_on_a_fresh_marker(self) -> None:
        assert due(Marker({}), cmday=6) == SOURCES

    def test_a_success_for_this_matchday_is_spent(self) -> None:
        marker = Marker({"voti": SourceRecord(at="x", cmday=6)})

        assert due(marker, cmday=6) == ("lega", "news")

    def test_a_success_for_another_matchday_does_not_count(self) -> None:
        """Keyed by matchday, not by a clock: "once a day" re-runs on a Tuesday that needs
        nothing and skips a Thursday postponement."""
        marker = Marker({"voti": SourceRecord(at="x", cmday=5)})

        assert "voti" in due(marker, cmday=6)

    def test_replanning_an_earlier_matchday_is_due_again(self) -> None:
        marker = Marker({"voti": SourceRecord(at="x", cmday=6)})

        assert "voti" in due(marker, cmday=5)

    def test_force_re_runs_a_spent_source(self) -> None:
        marker = Marker({s: SourceRecord(at="x", cmday=6) for s in SOURCES})

        assert due(marker, cmday=6, force=True) == SOURCES

    def test_only_narrows_to_what_was_named(self) -> None:
        assert due(Marker({}), cmday=6, only=("voti",)) == ("voti",)

    def test_an_unknown_source_name_selects_nothing(self) -> None:
        """`--source voto` must not silently become a full refresh against a live site."""
        assert due(Marker({}), cmday=6, only=("voto",)) == ()


class TestWhichGiornateTheVotiRunAsksFor:
    def test_the_giornata_being_planned_from_is_always_asked_for(self) -> None:
        """`voti_range` lists only giornate *short* of their fixtures, so a complete
        `cmday - 1` is absent from it — and a refresh that asked only for `short` would
        never fetch the giornata it exists for, never record a success, and run for ever."""
        assert voti_giornate(cmday=6, short=(), previous_calculated=True, cap=8) == (5,)

    def test_the_older_gaps_come_after_it(self) -> None:
        got = voti_giornate(cmday=6, short=(2, 4), previous_calculated=True, cap=8)

        assert got == (5, 2, 4)

    def test_nothing_is_asked_for_before_the_round_is_calculated(self) -> None:
        """Voti published before the lega calculates can still change (A20); a success
        recorded against them would make the history read fresh when it is not."""
        assert voti_giornate(cmday=6, short=(2, 4), previous_calculated=False, cap=8) == ()

    def test_the_first_matchday_has_nothing_before_it(self) -> None:
        assert voti_giornate(cmday=1, short=(), previous_calculated=True, cap=8) == ()

    def test_the_cap_never_pushes_out_the_giornata_that_matters(self) -> None:
        """A cold start with eight older gaps must still fetch `cmday - 1`."""
        got = voti_giornate(
            cmday=12, short=tuple(range(1, 11)), previous_calculated=True, cap=8
        )

        assert got[0] == 11
        assert len(got) == 8

    def test_a_giornata_at_or_after_the_one_being_planned_is_not_asked_for(self) -> None:
        got = voti_giornate(cmday=6, short=(6, 7, 3), previous_calculated=True, cap=8)

        assert got == (5, 3)


class TestWhenAVotiRunCounts:
    def test_rows_for_the_giornata_being_planned_from_are_the_success(self) -> None:
        assert voti_succeeded({5: 540, 2: 0}, cmday=6) is True

    def test_rows_for_older_gaps_alone_are_not(self) -> None:
        """Useful work, and it has not made the history fresh — so it is owed again."""
        assert voti_succeeded({2: 540, 4: 540}, cmday=6) is False

    def test_a_fetched_but_ungraded_giornata_is_not(self) -> None:
        assert voti_succeeded({5: 0}, cmday=6) is False


class TestTheRun:
    def test_every_due_source_runs_once_and_the_marker_records_it(self) -> None:
        sources, store = _Sources(), _MemoryMarker()

        report = _run(sources, store)

        assert sources.calls == ["voti", "lega", "news"]
        assert report.ok
        assert all(store.marker.succeeded(s, 6) for s in SOURCES)

    def test_a_source_already_done_is_not_run_again(self) -> None:
        store = _MemoryMarker(Marker({"voti": SourceRecord(at="x", cmday=6)}))
        sources = _Sources()

        report = _run(sources, store)

        assert "voti" not in sources.calls
        assert "voti" in report.already_done

    def test_a_failure_is_not_recorded_and_is_owed_again(self) -> None:
        sources = _Sources(lega=SourceOutcome("lega", "failed", "HTTPError"))
        store = _MemoryMarker()

        report = _run(sources, store)

        assert report.ok is False
        assert store.marker.succeeded("voti", 6)
        assert not store.marker.succeeded("lega", 6)
        assert "lega" in due(store.marker, cmday=6)

    def test_a_skipped_source_is_neither_recorded_nor_a_failure(self) -> None:
        sources = _Sources(news=SourceOutcome("news", "skipped", "disabled"))
        store = _MemoryMarker()

        report = _run(sources, store)

        assert report.ok
        assert not store.marker.succeeded("news", 6)
        assert "news: skipped - disabled" in report.lines()

    def test_force_re_runs_and_rewrites(self) -> None:
        store = _MemoryMarker(Marker({s: SourceRecord(at="old", cmday=6) for s in SOURCES}))
        sources = _Sources()

        _run(sources, store, force=True)

        assert sources.calls == ["voti", "lega", "news"]
        assert store.marker.records["voti"].at == NOW.isoformat()

    def test_the_marker_is_written_after_each_source_and_not_at_the_end(self) -> None:
        """A run killed half way keeps what it earned — `store_giornata`'s argument."""
        sources, store = _Sources(), _MemoryMarker()

        _run(sources, store)

        assert store.writes == 3

    def test_only_runs_just_what_was_named(self) -> None:
        sources, store = _Sources(), _MemoryMarker()

        _run(sources, store, only=("lega",))

        assert sources.calls == ["lega"]

    def test_the_voti_source_is_skipped_when_the_round_is_not_calculated(self) -> None:
        sources, store = _Sources(), _MemoryMarker()

        report = _run(sources, store, inputs=_inputs(previous_calculated=False))

        assert "voti" not in sources.calls
        assert [o.state for o in report.outcomes if o.source == "voti"] == ["skipped"]
        assert not store.marker.succeeded("voti", 6)

    def test_the_voti_source_is_handed_the_giornate_the_domain_chose(self) -> None:
        sources, store = _Sources(), _MemoryMarker()

        _run(sources, store, inputs=_inputs(cmday=6, short_giornate=(2,)))

        assert sources.giornate == (5, 2)


def _kickoff_in(hours: float) -> str:
    """An `mstr` that many hours after `NOW`, written the way the platform writes them."""
    return (NOW.astimezone(UTC) + timedelta(hours=hours)).replace(tzinfo=None).isoformat()


#: Each row puts `NOW` outside the window, one way per row, with a word from the reason A13
#: gives for it. Nine hours out is before the window opens; one hour out is after it closed.
OUTSIDE = [
    pytest.param({"mstr": _kickoff_in(9)}, "opens", id="too-early"),
    pytest.param({"mstr": _kickoff_in(1)}, "closed", id="too-late"),
    pytest.param({"status_mday": 7}, "not this matchday's", id="matchday-mismatch"),
    pytest.param({"mstr": "not a timestamp"}, "unreadable", id="unreadable-start"),
]


class TestTheNewsWindow:
    """A13's window, gating the news source the way `voti_giornate` gates voti.

    The window existed and was asserted ten times over in `test_deadline.py` while
    `in_news_window` had **no production caller** — the suite was green and could not see
    it. These tests are here because the gate is only real at this seam.
    """

    @pytest.mark.parametrize(("over", "says"), OUTSIDE)
    def test_the_source_is_never_reached_outside_the_window(
        self, over: dict[str, Any], says: str
    ) -> None:
        sources, store = _Sources(), _MemoryMarker()

        report = _run(sources, store, inputs=_inputs(**over))

        assert "news" not in sources.calls
        detail = next(o.detail for o in report.outcomes if o.source == "news")
        assert says in detail

    @pytest.mark.parametrize(("over", "says"), OUTSIDE)
    def test_a_window_skip_leaves_the_hour_still_owed(
        self, over: dict[str, Any], says: str
    ) -> None:
        """The defect this gate exists to prevent, stated as the thing that must not happen.

        `Marker.succeeded` matches on `cmday`, so one news success is spent per giornata. A
        stand-down that *recorded* one would suppress the run in the window — the hours the
        window exists to protect. `skipped` is not `ok`, and that is what keeps it owed.
        """
        sources, store = _Sources(), _MemoryMarker()

        report = _run(sources, store, inputs=_inputs(**over))

        assert not store.marker.succeeded("news", 6)
        assert "news" in due(store.marker, cmday=6)
        assert [o.state for o in report.outcomes if o.source == "news"] == ["skipped"]
        # And it is a stand-down, not a breakage: nothing here makes the run dirty.
        assert report.ok is True

    def test_the_open_window_reaches_the_source_and_spends_the_success(self) -> None:
        sources, store = _Sources(), _MemoryMarker()

        _run(sources, store, inputs=_inputs())

        assert "news" in sources.calls
        assert store.marker.succeeded("news", 6)
        assert "news" not in due(store.marker, cmday=6)

    def test_the_reason_is_the_domain_s_own_and_not_restated_here(self) -> None:
        """One implementation of "why not now": the application prints what the domain said.

        A reason composed at this layer would be a second answer to the question
        `news_window_cutoff` already answers, and the two would drift.
        """
        from fantabot.domain.lineup.deadline import news_window_cutoff

        inputs = _inputs(status_mday=7)
        report = _run(_Sources(), _MemoryMarker(), inputs=inputs)

        cutoff = news_window_cutoff(
            mstr=inputs.mstr, status_mday=7, plan_cmday=6, now=NOW
        )
        assert cutoff is not None
        assert [o.detail for o in report.outcomes if o.source == "news"] == [cutoff.reason]


class TestContainment:
    @pytest.mark.parametrize(
        "boom", [RuntimeError("connection refused"), OSError("no route"), ValueError("x")]
    )
    def test_any_ordinary_exception_is_one_failed_source(self, boom: Exception) -> None:
        sources = _Sources(lega=boom)
        store = _MemoryMarker()

        report = _run(sources, store)

        assert report.ok is False
        assert sources.calls == ["voti", "lega", "news"]
        assert store.marker.succeeded("news", 6)

    def test_only_the_type_name_is_recorded(self) -> None:
        """A message can carry the DSN, and this line goes to a log the operator pastes."""
        secret = "could not connect to postgresql://user:hunter2@host/db"
        sources = _Sources(lega=RuntimeError(secret))

        report = _run(sources, _MemoryMarker())

        assert [o.detail for o in report.outcomes if o.source == "lega"] == ["RuntimeError"]
        assert "hunter2" not in " ".join(report.lines())

    def test_an_assertion_error_propagates(self) -> None:
        """A bug is a bug. Absorbed, it would be a source that silently never worked."""
        sources = _Sources(voti=AssertionError("a real bug"))

        with pytest.raises(AssertionError, match="a real bug"):
            _run(sources, _MemoryMarker())

    def test_a_keyboard_interrupt_propagates(self) -> None:
        sources = _Sources(voti=KeyboardInterrupt())

        with pytest.raises(KeyboardInterrupt):
            _run(sources, _MemoryMarker())

    def test_a_marker_that_cannot_be_written_is_reported_and_not_raised(self) -> None:
        sources = _Sources()
        store = _MemoryMarker(fails=True)

        report = _run(sources, store)

        assert report.ok is False
        assert report.marker_error == "OSError"
        assert "marker: not written (OSError)" in report.lines()


class TestTheLiveSources:
    """The one implementation that reaches the outside, at the two points where it decides
    something rather than delegates. Nothing below runs a query or an agent."""

    def test_news_with_the_setting_off_makes_zero_agent_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A19(5). "Disabled" has to mean *nothing happened*, not "it ran and stored 0":
        the news run is the only source that spends money."""
        import fantabot.application.news_roster as roster
        from fantabot.application.lineup_refresh import LiveRefreshSources

        async def _explode(*_a: Any, **_kw: Any) -> Any:
            raise AssertionError("the news run was started")

        monkeypatch.setattr(roster, "fetch_roster_news", _explode)

        outcome = LiveRefreshSources(reporter=_Recorder(), news_enabled=False).news(_inputs())

        assert outcome == SourceOutcome("news", "skipped", "disabled")

    @pytest.mark.parametrize("source", ["environment", "dotenv"])
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1", True), ("true", True), ("TRUE", True), ("on", True), ("yes", True),
         (None, False), ("", False), ("0", False), ("no", False), ("maybe", False)],
    )
    def test_the_news_gate_fails_closed(
        self,
        news_setting: Callable[[str | None, str], None],
        raw: str | None,
        expected: bool,
        source: str,
    ) -> None:
        """AD4: parsed at use, and anything but an explicit yes leaves it off.

        Run twice over each value, once through each layer `live_setting` reads, because
        `_news_on` does not know which one answered and the rule must not either.

        This used to `monkeypatch.delenv` and stop there (audit 1.11). `config.live_setting`
        re-reads the `.env` **from disk** when the variable is absent from `os.environ` —
        deliberately, for the reason its own docstring records — and this repository's `.env`
        sets `FANTABOT_LINEUP_NEWS=on`, so the `None` case asserted `False` against a file
        that said `on`: it failed from the repository root and every case passed from any cwd
        without a `.env`. The suite's answer depended on the operator's `.env`, which is the
        one input a test of "anything but an explicit yes" must own outright.
        """
        from fantabot.application.lineup_refresh import LiveRefreshSources

        news_setting(raw, source)

        assert LiveRefreshSources(reporter=_Recorder())._news_on() is expected

    def test_the_gate_reads_the_env_file_at_all(
        self, news_setting: Callable[[str | None, str], None]
    ) -> None:
        """The `dotenv` half of the parametrize above is only worth running if it reaches
        the file, and a fixture that quietly wrote nowhere would make all ten of those cases
        pass for the reason the old test did: nothing set, gate off, `expected is False` for
        half of them. One positive, through the file alone, with `os.environ` empty."""
        from fantabot.application.lineup_refresh import LiveRefreshSources
        from fantabot.config import LINEUP_NEWS_VAR

        news_setting("on", "dotenv")

        assert LINEUP_NEWS_VAR not in os.environ
        assert LiveRefreshSources(reporter=_Recorder())._news_on() is True

    def test_an_exported_variable_outranks_the_env_file(
        self, news_setting: Callable[[str | None, str], None], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`live_setting`'s precedence, at the one gate that spends money on being wrong.
        The operator speaking later than the file wins — root `CLAUDE.md`, on
        `FANTABOT_HARVEST_DIR`: *"An exported variable still wins"*."""
        from fantabot.application.lineup_refresh import LiveRefreshSources
        from fantabot.config import LINEUP_NEWS_VAR

        news_setting("on", "dotenv")
        monkeypatch.setenv(LINEUP_NEWS_VAR, "no")

        assert LiveRefreshSources(reporter=_Recorder())._news_on() is False

    def test_news_with_no_roster_is_a_failure_and_not_a_silent_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import fantabot.application.news_roster as roster
        from fantabot.application.lineup_refresh import LiveRefreshSources

        async def _explode(*_a: Any, **_kw: Any) -> Any:
            raise AssertionError("the news run was started")

        monkeypatch.setattr(roster, "fetch_roster_news", _explode)
        source = LiveRefreshSources(reporter=_Recorder(), news_enabled=True)

        outcome = source.news(_inputs(roster_ids=()))

        assert outcome.state == "failed"
        assert outcome.detail == "no roster"

    def test_a_voti_run_that_missed_the_giornata_being_planned_from_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole rule, at the one place it is applied: three older gaps filled and the
        giornata the projection needs still empty is not a success."""
        import fantabot.application.scrape as scrape
        from fantabot.adapters.scraping.voti import GiornataCount
        from fantabot.application.lineup_refresh import LiveRefreshSources

        monkeypatch.setattr(
            scrape, "run_voti_range",
            lambda _s, giornate: [
                GiornataCount(giornata=g, teams_listed=20, teams_graded=20, rows=0 if g == 5 else 9)
                for g in giornate
            ],
        )

        outcome = LiveRefreshSources(reporter=_Recorder()).voti(_inputs(), [5, 2, 3])

        assert outcome.state == "failed"
        assert "no rows for g5" in outcome.detail

    def test_a_voti_run_that_filled_it_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import fantabot.application.scrape as scrape
        from fantabot.adapters.scraping.voti import GiornataCount
        from fantabot.application.lineup_refresh import LiveRefreshSources

        monkeypatch.setattr(
            scrape, "run_voti_range",
            lambda _s, giornate: [
                GiornataCount(giornata=g, teams_listed=20, teams_graded=20, rows=540)
                for g in giornate
            ],
        )

        outcome = LiveRefreshSources(reporter=_Recorder()).voti(_inputs(), [5])

        assert outcome.state == "ok"
        assert outcome.detail == "g5:540"


class TestTheMarkerOnDisk:
    def test_a_marker_survives_a_round_trip(self, tmp_path: Any) -> None:
        from fantabot.adapters.files.refresh_marker import FileMarkerStore

        store = FileMarkerStore(tmp_path / "lineup_refresh.json")
        store.write(Marker({}).with_success("voti", cmday=6, at=NOW.isoformat(), detail="g5:540"))

        assert FileMarkerStore(tmp_path / "lineup_refresh.json").read().succeeded("voti", 6)

    def test_a_missing_file_is_an_empty_marker_and_not_an_error(self, tmp_path: Any) -> None:
        """A refusal to start means the refresh never runs and the projection quietly goes
        stale; an empty marker costs an hour's requests. The cheap failure is the right one."""
        from fantabot.adapters.files.refresh_marker import FileMarkerStore

        assert FileMarkerStore(tmp_path / "nothing.json").read().records == {}

    @pytest.mark.parametrize("junk", ["", "{", "[]", '{"voti": 3}', '{"voti": {"at": 1}}'])
    def test_a_file_that_is_not_a_marker_is_an_empty_marker(self, tmp_path: Any, junk: str) -> None:
        from fantabot.adapters.files.refresh_marker import FileMarkerStore

        path = tmp_path / "lineup_refresh.json"
        path.write_text(junk, encoding="utf-8")

        assert FileMarkerStore(path).read().records == {}

    def test_a_half_written_record_does_not_take_the_others_down(self, tmp_path: Any) -> None:
        from fantabot.adapters.files.refresh_marker import FileMarkerStore

        path = tmp_path / "lineup_refresh.json"
        path.write_text('{"voti": {"at": "x", "cmday": 6}, "lega": {"at": "y"}}', encoding="utf-8")

        marker = FileMarkerStore(path).read()

        assert marker.succeeded("voti", 6)
        assert "lega" not in marker.records

    def test_the_directory_is_created(self, tmp_path: Any) -> None:
        from fantabot.adapters.files.refresh_marker import FileMarkerStore

        store = FileMarkerStore(tmp_path / "deep" / "lineup_refresh.json")
        store.write(Marker({}).with_success("voti", cmday=6, at=NOW.isoformat()))

        assert store.path.exists()
