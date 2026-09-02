"""`application/lega_sync` — the failure model, and that a partial read still writes.

Every `apileague` call is monkeypatched; the suite opens no sockets. What is asserted
here is not the happy path (the parsers own that) but the behaviour that only shows up
when the platform misbehaves, which is the reason this module has a failure list at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from fantabot.application import lega_sync
from fantabot.application.reporting import SilentReporter
from fantabot.domain.tokens.errors import TokenExpired

STATUS = {"sId": 21, "mday": 3, "mstr": "2026-09-04T18:45:00", "activ": True, "sto": False}
ROSTERS = {"budg": 500, "xsltc": 32, "sroles": 2, "minrl": [2, 23], "maxrl": [4, 28]}
LINEUP = {"mods": ["3412"], "tbench": 12, "lcap": 3}
TEAM = {"id": 1, "idu": 9, "n": "Team C", "nu": "me", "d": "A",
        "cri": 500, "crs": 3, "cr": 497, "cal": "10;11", "cs": "2;1"}
COMPS = [{"id": 311681, "name": "Fanta", "tmids": [1], "del": False}]
CALENDAR = [{"matchDay": 1, "championshipMatchDay": 3, "calculated": False,
             "matches": [{"tIdH": 1, "tIdA": 2, "result": "-"}]}]
ROLES = [{"id": 254, "name": "Dimarco", "team": "Inter", "originalRole": 2, "role": 3}]
POOL = {"players": [{"id": 10, "name": "Malen", "stnme": "ROM", "quotd": 1,
                     "fvmfc": 207, "fvmma": 207, "marle": [16]}]}


def _install(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    """Patch every read `collect` makes. An override may be a value or an exception."""
    defaults = {
        "league_status": STATUS, "roster_settings": ROSTERS, "lineup_settings": LINEUP,
        "teams": [TEAM], "competitions": COMPS, "calendar": CALENDAR,
        "custom_roles": ROLES, "players": POOL,
    }
    defaults.update(overrides)
    for name, value in defaults.items():
        def call(*_args: Any, _v: Any = value, **_kwargs: Any) -> Any:
            if isinstance(_v, Exception):
                raise _v
            return _v
        monkeypatch.setattr(lega_sync.apileague, name, call)


def test_collect_reads_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)
    result = lega_sync.collect(4103937, store=None, reporter=SilentReporter())  # type: ignore[arg-type]
    assert result.ok
    assert result.state is not None and result.state.matchday == 3
    assert len(result.rosters) == 1 and len(result.rosters[0].roster) == 2
    assert len(result.fixtures) == 1
    assert len(result.pool) == 1 and result.pool[0].ruoli_codice == ("PC",)


def test_one_failed_read_does_not_cost_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pool is megabytes and the calendar is the only place results appear. A sync
    that abandons both because `custom-roles` returned 400 is a sync nobody runs."""
    _install(monkeypatch, custom_roles=RuntimeError("boom"))
    result = lega_sync.collect(4103937, store=None, reporter=SilentReporter())  # type: ignore[arg-type]
    assert result.failures == ["custom-roles: RuntimeError"]
    assert not result.ok
    assert result.custom_roles == ()
    assert result.pool and result.fixtures and result.rosters


def test_a_failed_settings_read_still_yields_a_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """`status` is what makes a state row worth writing; the settings enrich it. Losing
    the enrichment must not lose the matchday."""
    _install(monkeypatch, roster_settings=RuntimeError("boom"))
    result = lega_sync.collect(4103937, store=None, reporter=SilentReporter())  # type: ignore[arg-type]
    assert result.state is not None and result.state.matchday == 3
    assert result.state.budget is None


def test_a_token_failure_is_raised_not_collected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired token fails all eight reads for one reason. Reporting it eight times as
    eight unrelated outages buries the single thing the operator has to do: re-auth."""
    _install(monkeypatch, league_status=TokenExpired(4103937, "2026-08-01"))
    with pytest.raises(TokenExpired):
        lega_sync.collect(4103937, store=None, reporter=SilentReporter())  # type: ignore[arg-type]


def test_a_bad_roster_pairing_is_a_failure_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cal`/`cs` disagreeing raises out of the parser. It has to land in the failure
    list like a 400 would, or one malformed team aborts the whole sync."""
    _install(monkeypatch, teams=[{**TEAM, "cs": "2"}])
    result = lega_sync.collect(4103937, store=None, reporter=SilentReporter())  # type: ignore[arg-type]
    assert result.rosters == ()
    assert any(f.startswith("teams/parse") for f in result.failures)
    assert result.pool


def test_a_deleted_competition_is_not_asked_for_its_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[int] = []

    def calendar(_league: int, competition_id: int, **_kwargs: Any) -> Any:
        asked.append(competition_id)
        return CALENDAR

    _install(monkeypatch, competitions=[*COMPS, {"id": 177318, "name": "old", "del": True}])
    monkeypatch.setattr(lega_sync.apileague, "calendar", calendar)
    lega_sync.collect(4103937, store=None, reporter=SilentReporter())  # type: ignore[arg-type]
    assert asked == [311681]


class _Repo:
    """Counts what `persist` asks for. No session, no Postgres."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def record_league_state(self, _state: Any) -> None:
        self.calls.append("state")

    def record_team_rosters(self, rosters: Any) -> int:
        self.calls.append("rosters")
        return len(rosters)

    def record_competitions(self, comps: Any) -> int:
        self.calls.append("competitions")
        return len(comps)

    def upsert_fixtures(self, fixtures: Any) -> int:
        self.calls.append("fixtures")
        return len(fixtures)

    def record_custom_roles(self, roles: Any) -> int:
        self.calls.append("roles")
        return len(roles)

    def record_pool(self, pool: Any) -> int:
        self.calls.append("pool")
        return len(pool)


def test_persist_writes_only_what_was_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, custom_roles=RuntimeError("boom"), players=RuntimeError("boom"))
    result = lega_sync.collect(4103937, store=None, reporter=SilentReporter())  # type: ignore[arg-type]
    repo = _Repo()
    written = lega_sync.persist(result, repo)  # type: ignore[arg-type]
    assert "roles" not in repo.calls and "pool" not in repo.calls
    assert written == {
        "league_snapshot": 1, "league_team_snapshot": 1,
        "league_competition": 1, "league_fixture": 1,
    }
