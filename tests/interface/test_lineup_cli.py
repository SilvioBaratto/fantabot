"""`fantabot lineup` — read/plan/submit the weekly Mantra formazione. **Zero sockets.**

The network call (`apileague.teamLineup_read`) and the database session are both faked, so
the shell is exercised without Postgres or a token, in the socket-free default tier. The
pure formatter is tested directly; the command is a thin wrapper around it.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from fantabot.domain.lineup.models import PlannedLineup
from fantabot.interface.app import app
from fantabot.interface.lineup import (
    format_freshness,
    format_lineup,
    format_plan,
    format_projection_rows,
    is_past_deadline,
)

runner = CliRunner()

DTO = {
    "mdl": "343",
    "starts": [6482, 2788, 7564, 7274, 7181, 1850, 5504, 5678, 2194, 6875, 4179],
    "bench": [4360, 5750, 4137, 4998, 5620, 5680, 4459, 6898, 7198, 4947, 5319, 7126],
}


# --- the pure formatter ---------------------------------------------------


def test_format_lineup_names_the_module_and_counts_the_lines() -> None:
    text = "\n".join(format_lineup(DTO))

    assert "343" in text
    assert "11" in text  # starters
    assert "12" in text  # bench
    assert "6482" in text


def test_format_lineup_says_when_no_lineup_is_set() -> None:
    assert "no lineup" in " ".join(format_lineup({})).lower()


# --- the CLI shell --------------------------------------------------------


class _Session:
    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


def _fakes(monkeypatch: pytest.MonkeyPatch, dto: dict[str, Any] = DTO) -> None:
    from fantabot import config
    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(
        config.settings, "fantabot_encryption_key", Fernet.generate_key().decode()
    )
    # Hermetic: the commands resolve the lega from config when no --league is given, and the
    # real value lives in the gitignored .env — absent in CI. Pin it so the tests do not depend
    # on a local .env.
    monkeypatch.setattr(config.settings, "fantabot_league_id", 4103937)
    monkeypatch.setattr(database_manager, "_session_factory", _Session)
    monkeypatch.setattr(
        apileague, "teamLineup_read", lambda *a, **k: {"teamLineupDto": dto, "lineUpInfo": []}
    )


def test_show_renders_the_current_lineup(monkeypatch: pytest.MonkeyPatch) -> None:
    _fakes(monkeypatch)

    result = runner.invoke(app, ["lineup", "show", "--competition", "311681"])

    assert result.exit_code == 0
    assert "343" in result.output


def test_show_requires_a_competition(monkeypatch: pytest.MonkeyPatch) -> None:
    _fakes(monkeypatch)

    result = runner.invoke(app, ["lineup", "show"])

    assert result.exit_code != 0
    assert "competition" in result.output.lower()


# --- plan: full wiring over a real, fieldable roster ----------------------


def test_format_plan_names_the_module_matchday_and_players() -> None:
    plan = PlannedLineup(
        module="343", starts=(1, 2), bench=(3, 4), competition=311681, mday=1, cmday=3, tid=9
    )
    lines = format_plan(plan, {1: "Alpha", 2: "Bravo", 3: "Charlie", 4: "Delta"})

    assert "343" in lines[0]
    assert "matchday 1" in lines[0]
    assert "Alpha, Bravo" in lines[1]


# 3 keepers + 27 broad-role outfielders: fields 3-4-3 and fills a 12-man bench.
_LINEUP_INFO = [
    {"pid": 1000 + i, "role": [6], "indexCompare": 5.0 - 0.1 * i, "plyr": f"GK{i}"}
    for i in range(3)
] + [
    {
        "pid": 2000 + i,
        "role": [7, 8, 9, 10, 11, 12, 13, 14, 15, 16],  # broad: fields back-3 and back-4 modules
        "indexCompare": 8.0 - 0.1 * i,
        "plyr": f"Player{i}",
    }
    for i in range(27)
]


@pytest.fixture(autouse=True)
def _never_the_operators_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every test in this file runs against a throwaway `HOME`.

    `config.lineup_runs_path()` is `~/.fantabot/lineup_runs.jsonl`, and a `lineup submit
    --scheduled` writes one line there — so two tests that exercised the flag without
    repointing `HOME` appended **34 records of `GK0`/`Player0`** into the operator's real
    run log, where the app renders them as submits that happened. Autouse rather than a
    helper each test remembers to call: the tests that did the damage were the two that
    did not call it.
    """
    monkeypatch.setenv("HOME", str(tmp_path))


def _fakes_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    from fantabot import config
    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(
        config.settings, "fantabot_encryption_key", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(config.settings, "fantabot_league_id", 4103937)  # hermetic; see _fakes
    monkeypatch.setattr(database_manager, "_session_factory", _Session)
    monkeypatch.setattr(apileague, "my_team", lambda *a, **k: {"id": 10000003})
    monkeypatch.setattr(
        apileague,
        "competitions",
        lambda *a, **k: [{"id": 311681, "tmids": [10000003], "del": False}],
    )
    monkeypatch.setattr(
        apileague,
        "teamLineup_read",
        lambda *a, **k: {
            # `tid: 0` **on purpose**, and it must not equal `my_team`'s id. The DTO is
            # empty when a competition has no saved lineup, so 0 is what a run that read
            # `tid` from here would actually submit — the regression the invariant exists
            # for. While the two sources held the same value, no assertion could tell which
            # one `build_plans` had used.
            "teamLineupDto": {"mday": 1, "cmday": 3, "tid": 0},
            "lineUpInfo": _LINEUP_INFO,
        },
    )
    monkeypatch.setattr(
        apileague, "lineup_settings", lambda *a, **k: {"mods": ["343"], "tbench": 12}
    )
    # sroles=2 -> Mantra, the format these fixtures are in (marle-coded lineUpInfo).
    monkeypatch.setattr(apileague, "roster_settings", lambda *a, **k: {"sroles": 2})
    monkeypatch.setattr(
        apileague,
        "calculate_settings",
        lambda *a, **k: {
            "bnMls": {
                "bmgs": 3.0, "bmass": 1.0, "bmass2": 1.0, "bmass3": 1.0, "bmyc": -0.5,
                "bmrc": -1.0, "bmog": -2.0, "bmpsc": 3.0, "bmpns": -3.0, "bmpsa": 3.0,
                "bmgc": -1.0, "motm": 1.0, "bmcsh": 0.0, "bmdg": 0.0,
            },
            "step": {"stlmt": 66.0, "stgoal": [6.0, 6.0, 6.0, 6.0]},
        },
    )


def test_plan_builds_and_prints_a_legal_formation(monkeypatch: pytest.MonkeyPatch) -> None:
    _fakes_plan(monkeypatch)

    result = runner.invoke(app, ["lineup", "plan"])  # competition auto-resolved

    assert result.exit_code == 0, result.output
    assert "343" in result.output
    assert "XI:" in result.output
    assert "bench:" in result.output


# --- submit: two locks, dry run by default --------------------------------


def test_is_past_deadline_compares_naive() -> None:
    assert is_past_deadline("2020-01-01T00:00:00", datetime(2026, 1, 1)) is True
    assert is_past_deadline("2030-01-01T00:00:00", datetime(2026, 1, 1)) is False
    assert is_past_deadline("nonsense", datetime(2026, 1, 1)) is False


def _set_auto_act(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    """Arm the ambient lock the way the world does, not by writing to the singleton.

    These tests used to `monkeypatch.setattr(config.settings, "fantabot_auto_act", ...)`.
    That worked because `decide_arming` read that attribute — which was the defect: the
    singleton is built once at import, so a long-lived app server could not be disarmed by
    editing `.env`. Setting it here would now be setting a value nothing reads, and the
    armed-path tests would silently stop testing the armed path.

    An environment variable, with nothing recorded as injected from a `.env`, is the
    "genuinely exported" branch of `config.live_auto_act` — the one that outranks the file.
    """
    from fantabot import config

    monkeypatch.setattr(config, "_DOTENV_INJECTED", {})
    monkeypatch.setenv(config.AUTO_ACT_VAR, "true" if on else "false")


def _submit_fakes(monkeypatch: pytest.MonkeyPatch, *, auto_act: bool) -> list[Any]:
    from fantabot.adapters.http import apileague

    _fakes_plan(monkeypatch)
    _set_auto_act(monkeypatch, auto_act)
    monkeypatch.setattr(
        apileague, "league_status", lambda *a, **k: {"mstr": "2099-01-01T00:00:00"}
    )
    posted: list[Any] = []
    monkeypatch.setattr(
        apileague, "teamLineup_submit", lambda _lid, body, **k: posted.append(body)
    )
    return posted


def test_submit_is_a_dry_run_when_auto_act_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = _submit_fakes(monkeypatch, auto_act=False)

    result = runner.invoke(app, ["lineup", "submit", "--arm"])

    assert result.exit_code == 0
    assert "dry run" in result.output
    assert posted == [], "submitted despite AUTO_ACT being off"


def test_submit_is_refused_without_arm_even_when_auto_act_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _submit_fakes(monkeypatch, auto_act=True)

    result = runner.invoke(app, ["lineup", "submit"])  # no --arm

    assert result.exit_code == 0
    assert "dry run" in result.output
    assert posted == []


def test_submit_posts_and_reads_back_when_fully_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _submit_fakes(monkeypatch, auto_act=True)

    result = runner.invoke(app, ["lineup", "submit", "--arm"])

    assert result.exit_code == 0, result.output
    assert len(posted) == 1, "an armed submit must POST exactly once"
    assert posted[0]["mdl"] == "343"
    assert "submitted" in result.output


def test_submit_refuses_when_the_matchday_context_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fantabot.adapters.http import apileague

    posted = _submit_fakes(monkeypatch, auto_act=True)
    # first-of-season: roster present via lineUpInfo, but the DTO has no mday/cmday
    monkeypatch.setattr(
        apileague,
        "teamLineup_read",
        lambda *a, **k: {"teamLineupDto": {}, "lineUpInfo": _LINEUP_INFO},
    )

    result = runner.invoke(app, ["lineup", "submit", "--arm"])

    assert result.exit_code == 1
    assert "no matchday context" in result.output
    assert posted == [], "must not POST with zero coordinates"


def test_submit_exits_one_when_every_module_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fantabot.adapters.http import apileague
    from fantabot.domain.lineup.errors import LineupRejected

    _fakes_plan(monkeypatch)
    monkeypatch.setattr(
        apileague, "lineup_settings", lambda *a, **k: {"mods": ["442", "343"], "tbench": 12}
    )
    _set_auto_act(monkeypatch, True)
    monkeypatch.setattr(
        apileague, "league_status", lambda *a, **k: {"mstr": "2099-01-01T00:00:00"}
    )

    def _always_reject(_lid: Any, body: dict[str, Any], **_k: Any) -> None:
        raise LineupRejected("LUP009")

    monkeypatch.setattr(apileague, "teamLineup_submit", _always_reject)

    result = runner.invoke(app, ["lineup", "submit", "--arm"])

    assert result.exit_code == 1
    assert "every fieldable module was refused" in result.output
    assert "submitted" not in result.output


def test_submit_reports_cleanly_when_the_roster_fields_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fantabot.adapters.http import apileague

    _fakes_plan(monkeypatch)
    _set_auto_act(monkeypatch, True)
    monkeypatch.setattr(  # empty roster -> NoFieldableModule, caught as a LineupError
        apileague,
        "teamLineup_read",
        lambda *a, **k: {"teamLineupDto": {"mday": 1, "cmday": 3}, "lineUpInfo": []},
    )

    result = runner.invoke(app, ["lineup", "submit", "--arm"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output  # a clean LineupError, not a crash


def test_submit_falls_back_to_the_next_module_on_a_platform_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fantabot.adapters.http import apileague
    from fantabot.domain.lineup.errors import LineupRejected

    _fakes_plan(monkeypatch)
    # a roster that fields more than one module, so there is a next-best to fall to
    monkeypatch.setattr(
        apileague, "lineup_settings", lambda *a, **k: {"mods": ["442", "343"], "tbench": 12}
    )
    _set_auto_act(monkeypatch, True)
    monkeypatch.setattr(
        apileague, "league_status", lambda *a, **k: {"mstr": "2099-01-01T00:00:00"}
    )

    tried: list[str] = []

    def _submit(_lid: Any, body: dict[str, Any], **_k: Any) -> None:
        tried.append(body["mdl"])
        if len(tried) == 1:
            raise LineupRejected("LUP009")  # platform refuses the first (best) module

    monkeypatch.setattr(apileague, "teamLineup_submit", _submit)

    result = runner.invoke(app, ["lineup", "submit", "--arm"])

    assert result.exit_code == 0, result.output
    assert len(tried) == 2, "it must fall back to the next module after a refusal"
    assert "trying the next module" in result.output
    assert "submitted" in result.output


def test_submit_reports_the_submit_when_the_read_back_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 0 and a loud caveat, not exit 1 and a denial.

    The POST returned 200, so the lineup is on the platform. Exiting non-zero would send a
    cron wrapper back to re-POST the lineup it just saved — idempotent while the round is
    open, refused once it closes, and either way the operator's last signal was "failed"
    about something that succeeded.
    """
    from fantabot.adapters.http import apileague
    from fantabot.domain.tokens.errors import ApiTimeout

    posted = _submit_fakes(monkeypatch, auto_act=True)

    # Only the **confirming** read may fail. `build_plans` reads the same endpoint first, so
    # patching it outright breaks the run before anything is ever POSTed — which would make
    # this a test about planning, not about losing the evidence of a submit.
    planning_read = apileague.teamLineup_read

    def timing_out(*a: Any, **k: Any) -> dict[str, Any]:
        if posted:
            raise ApiTimeout(10)
        return planning_read(*a, **k)  # type: ignore[no-any-return]

    monkeypatch.setattr(apileague, "teamLineup_read", timing_out)

    result = runner.invoke(app, ["lineup", "submit", "--arm"])

    assert result.exit_code == 0, result.output
    assert len(posted) == 1, "it re-POSTed to chase its evidence"
    assert "submitted" in result.output
    assert "unconfirmed" in result.output
    assert "10s" in result.output


def test_plan_and_submit_build_their_plans_through_the_same_door(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One implementation of the seven reads, reached by both commands.

    `interface/lineup.py` kept a private `_build_plans` — the same reads, the same `sroles`
    format detection, the same `tid` source — and it was what `lineup plan` ran, while
    `lineup submit`, `GET /lineup/plan` and `POST /lineup/submit` all ran
    `application/lineup_submit.build_plans`. Byte-identical bodies when written, and
    nothing kept them that way.

    That is the shape the lift's own commit warned about: `d74321a` says `build_plans`
    "lifts with it as a **third** call site, not a second: `GET /lineup/plan` already
    reimplemented it by hand, which is how the app came to read the format from a different
    place than the command did." The fourth copy was left behind.

    Substituting the one function must change what **both** commands print. A test that
    only checked `submit` would have passed throughout the whole period the copy existed.
    """
    from fantabot.application import lineup_submit

    _fakes_plan(monkeypatch)
    called: list[str] = []

    real = lineup_submit.build_plans

    def one_door(store: Any, league_id: int, competition: int) -> Any:
        # Delegates, then rewrites the names. The marker travels through whatever the
        # command prints, so this proves the *return value* is used and not merely that the
        # function was entered — a spy that only counted calls would still pass with a
        # second implementation sitting next to it.
        called.append("build_plans")
        plans, names, comp = real(store, league_id, competition)
        return plans, dict.fromkeys(names, "Sentinel"), comp

    monkeypatch.setattr(lineup_submit, "build_plans", one_door)

    for command in (["lineup", "plan"], ["lineup", "submit"]):
        called.clear()
        result = runner.invoke(app, command)

        assert called == ["build_plans"], f"{command} did not go through build_plans"
        assert "Sentinel" in result.output, (
            f"{command} printed names from somewhere other than build_plans: {result.output!r}"
        )


def test_the_submitted_tid_comes_from_my_team_not_the_lineup_dto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first hop of `tid`, which was the one hop nothing tested.

    `CLAUDE.md`: *"`tid` comes from `my_team`, not from the lineup DTO, which is empty when
    a competition has no saved lineup — read there, it submits `tid=0`."*

    `test_tid_comes_from_the_argument_not_the_empty_dto` names the rule but asserts one
    layer down, on `inputs_from_lineup(dto, ..., tid=999)`: it proves the argument beats the
    DTO and is structurally blind to which value `build_plans` passes as that argument. The
    value travels `build_plans` -> `inputs_from_lineup` -> `LineupInputs.tid` ->
    `PlannedLineup.tid` -> `payload.build()["tid"]` -> the POST body, and only the first hop
    was unpinned. Sourcing `tid` from the DTO passed all 1886 tests.

    So this asserts on the body that would go to the platform.
    """
    posted = _submit_fakes(monkeypatch, auto_act=True)

    result = runner.invoke(app, ["lineup", "submit", "--arm"])

    assert result.exit_code == 0, result.output
    assert posted[0]["tid"] == 10000003, (
        "the submitted tid did not come from my_team — a DTO read would send 0 and the "
        "platform would file the lineup against no team"
    )


def test_a_scheduled_submit_after_the_start_exits_zero_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--scheduled` is the `launchd` job's flag. After the start it is a normal skip, not a
    failure: exit 0, nothing POSTed, and a sentence saying why."""
    from fantabot.adapters.http import apileague

    posted = _submit_fakes(monkeypatch, auto_act=True)
    # `_fakes_plan`'s lineup is Serie A matchday 3 (`cmday`), so the status must be too.
    monkeypatch.setattr(
        apileague, "league_status", lambda *a, **k: {"mstr": "2020-01-01T00:00:00", "mday": 3}
    )

    result = runner.invoke(app, ["lineup", "submit", "--arm", "--scheduled"])

    assert result.exit_code == 0, result.output
    assert posted == [], "a scheduled run reshuffled a lineup in play"
    assert "skipped" in result.output and "kicked off" in result.output


def test_a_scheduled_submit_before_the_start_submits(monkeypatch: pytest.MonkeyPatch) -> None:
    from fantabot.adapters.http import apileague

    posted = _submit_fakes(monkeypatch, auto_act=True)
    monkeypatch.setattr(
        apileague, "league_status", lambda *a, **k: {"mstr": "2099-01-01T00:00:00", "mday": 3}
    )

    result = runner.invoke(app, ["lineup", "submit", "--arm", "--scheduled"])

    assert result.exit_code == 0, result.output
    assert len(posted) == 1


# -- the record every scheduled run leaves -----------------------------------------------


def _records(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Where the record lands. `HOME` is already the throwaway one, autouse, above."""
    return tmp_path / ".fantabot" / "lineup_runs.jsonl"


def test_a_scheduled_run_leaves_exactly_one_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fantabot.adapters.files.lineup_runs import SUBMITTED, read_runs
    from fantabot.adapters.http import apileague

    log = _records(monkeypatch, tmp_path)
    _submit_fakes(monkeypatch, auto_act=True)
    monkeypatch.setattr(
        apileague, "league_status", lambda *a, **k: {"mstr": "2099-01-01T00:00:00", "mday": 3}
    )

    result = runner.invoke(app, ["lineup", "submit", "--arm", "--scheduled"])

    assert result.exit_code == 0, result.output
    runs, skipped = read_runs(log)
    assert skipped == 0 and len(runs) == 1
    assert runs[0].status == SUBMITTED
    assert runs[0].scheduled is True and runs[0].league == 4103937


def test_a_run_that_fails_before_any_plan_is_still_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The failures most worth a record are the ones that stop everything else. A dead
    token raises out of the very first read — no outcome, no plan — and a record written
    only from an outcome would say nothing on the Saturday that mattered."""
    from fantabot.adapters.files.lineup_runs import FAILED, read_runs
    from fantabot.adapters.http import apileague
    from fantabot.domain.tokens.errors import TokenMissing

    log = _records(monkeypatch, tmp_path)
    _submit_fakes(monkeypatch, auto_act=True)

    def dead(*_a: Any, **_k: Any) -> Any:
        raise TokenMissing(4103937)

    monkeypatch.setattr(apileague, "my_team", dead)

    result = runner.invoke(app, ["lineup", "submit", "--arm", "--scheduled"])

    assert result.exit_code == 1, result.output
    [run] = read_runs(log)[0]
    assert run.status == FAILED and run.code == "TokenMissing"
    assert "auth login" in run.detail


def test_an_unreachable_database_is_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reboot case: the bundled Postgres is not up, and the token lives in it."""
    from sqlalchemy.exc import OperationalError

    from fantabot.adapters.files.lineup_runs import FAILED, read_runs
    from fantabot.adapters.persistence import database_manager

    log = _records(monkeypatch, tmp_path)
    _submit_fakes(monkeypatch, auto_act=True)

    def down() -> Any:
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("no server"))

    monkeypatch.setattr(database_manager, "get_session", down)

    result = runner.invoke(app, ["lineup", "submit", "--arm", "--scheduled"])

    assert result.exit_code == 1, result.output
    [run] = read_runs(log)[0]
    assert run.status == FAILED and run.code == "database-unreachable"


def test_a_manual_run_leaves_no_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The history is the automation's. A person at the terminal already saw the answer."""
    log = _records(monkeypatch, tmp_path)
    _submit_fakes(monkeypatch, auto_act=True)

    runner.invoke(app, ["lineup", "submit", "--arm"])

    assert not log.exists()


def test_the_record_is_stamped_by_the_lineup_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_now` is the lineup surface's one clock read, and the clock guard counts it. A
    record stamped by a second `datetime.now()` would be a second seam the parity tier
    cannot freeze."""
    from fantabot.adapters.files.lineup_runs import read_runs
    from fantabot.adapters.http import apileague
    from fantabot.interface import lineup as lineup_cli

    log = _records(monkeypatch, tmp_path)
    _submit_fakes(monkeypatch, auto_act=True)
    monkeypatch.setattr(
        apileague, "league_status", lambda *a, **k: {"mstr": "2099-01-01T00:00:00", "mday": 3}
    )
    monkeypatch.setattr(lineup_cli, "_now", lambda: datetime(2026, 9, 12, 10, 0))

    runner.invoke(app, ["lineup", "submit", "--arm", "--scheduled"])

    [run] = read_runs(log)[0]
    assert run.at.startswith("2026-09-12T10:00")


# --- plan --model projection: the preview, and only the preview ------------


def _projection_report() -> Any:
    """What `projection_for_league` returns, canned: the heavy read is its own test's."""
    from fantabot.application.lineup_projection import (
        PlayerLine,
        ProjectionOutcome,
        ProjectionReport,
    )
    from fantabot.domain.lineup.freshness import Freshness

    plan = PlannedLineup(
        module="343", starts=tuple(DTO["starts"]), bench=tuple(DTO["bench"]),
        competition=311681, mday=1, cmday=3, tid=10000003,
    )
    lines = (
        PlayerLine(6482, "GK", "P", 6.10, 0.98, 1.20, 6.05),
        PlayerLine(4179, "ATT", "A", 7.40, 0.60, 2.10, 6.30),
        # On the bench in `DTO`: the column has to tell them apart.
        PlayerLine(4360, "GK", "P", 5.20, 0.40, 1.80, 4.90),
    )
    return ProjectionReport(
        baseline=(plan,),
        projection=ProjectionOutcome(
            plans=(plan,),
            lines=lines,
            freshness=Freshness(False, ("no voti refresh recorded after g2 was calculated",), ()),
            replacement=5.5,
            as_of=date(2026, 9, 22),
            seasons=("2022/23", "2023/24", "2024/25", "2025/26", "2026/27"),
        ),
        names={6482: "Sommer", 4179: "Lautaro"},
        competition=311681,
    )


def test_plan_projection_prints_both_models_the_table_and_the_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fantabot.application import lineup_projection

    _fakes_plan(monkeypatch)
    monkeypatch.setattr(
        lineup_projection, "projection_for_league", lambda *a, **k: _projection_report()
    )

    result = runner.invoke(app, ["lineup", "plan", "--model", "projection"])

    assert result.exit_code == 0, result.output
    assert "indexcompare" in result.output and "projection" in result.output
    assert "STALE" in result.output and "no voti refresh" in result.output
    assert "Sommer" in result.output and "Lautaro" in result.output
    assert "sigma~" in result.output and "5.50" in result.output  # the replacement level


def test_plan_projection_says_so_when_the_history_cannot_carry_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`project` refuses thin history rather than guessing; the command prints its reason."""
    from fantabot.application import lineup_projection

    _fakes_plan(monkeypatch)

    def _refuse(*a: Any, **k: Any) -> Any:
        raise ValueError("history too thin: no player has two appearances to vary between")

    monkeypatch.setattr(lineup_projection, "projection_for_league", _refuse)

    result = runner.invoke(app, ["lineup", "plan", "--model", "projection"])

    assert result.exit_code == 1
    assert "no projection" in result.output and "too thin" in result.output


def test_plan_refuses_a_model_it_does_not_have(monkeypatch: pytest.MonkeyPatch) -> None:
    _fakes_plan(monkeypatch)

    result = runner.invoke(app, ["lineup", "plan", "--model", "vibes"])

    assert result.exit_code == 1
    assert "unknown model" in result.output


def test_the_default_model_is_the_platform_s_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--model indexcompare` is the default path, unchanged: no projection import, no
    numpy, and the same output as no flag at all."""
    from fantabot.application import lineup_projection

    _fakes_plan(monkeypatch)
    monkeypatch.setattr(
        lineup_projection,
        "projection_for_league",
        lambda *a, **k: pytest.fail("the default model must not reach the projection"),
    )

    bare = runner.invoke(app, ["lineup", "plan"])
    named = runner.invoke(app, ["lineup", "plan", "--model", "indexcompare"])

    assert bare.exit_code == 0 and named.exit_code == 0
    assert bare.output == named.output


def test_the_table_marks_the_xi_and_rounds_to_two_places() -> None:
    header, *rows = format_projection_rows(_projection_report())

    assert header == ("player", "role", "mu", "p", "sigma~", "value", "XI")
    assert rows[0] == ("Sommer", "P", "6.10", "0.98", "1.20", "6.05", "XI")
    assert rows[1][-1] == "XI"  # 4179 starts too
    assert rows[2] == ("4360", "P", "5.20", "0.40", "1.80", "4.90", "")  # benched, unnamed


def test_a_fresh_verdict_carries_no_reasons() -> None:
    from fantabot.domain.lineup.freshness import Freshness

    assert format_freshness(Freshness(True, (), ("g16 6/10 (postponed?)",))) == [
        "data: fresh",
        "  warning: g16 6/10 (postponed?)",
    ]
