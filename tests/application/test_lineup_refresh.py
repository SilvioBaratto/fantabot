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

from collections.abc import Sequence
from datetime import date, datetime
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


def _inputs(**over: Any) -> RefreshInputs:
    base: dict[str, Any] = {
        "league_id": 4103937,
        "season": "2026/27",
        "cmday": 6,
        "day": DAY,
        "roster_ids": (1, 2, 3),
        "short_giornate": (),
        "previous_calculated": True,
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

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1", True), ("true", True), ("TRUE", True), ("on", True), ("yes", True),
         (None, False), ("", False), ("0", False), ("no", False), ("maybe", False)],
    )
    def test_the_news_gate_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: bool
    ) -> None:
        """AD4: parsed at use, and anything but an explicit yes leaves it off."""
        from fantabot.application.lineup_refresh import LiveRefreshSources

        monkeypatch.delenv("FANTABOT_LINEUP_NEWS", raising=False)
        if raw is not None:
            monkeypatch.setenv("FANTABOT_LINEUP_NEWS", raw)

        assert LiveRefreshSources(reporter=_Recorder())._news_on() is expected

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
