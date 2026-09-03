"""`fantabot lineup` — read/plan/submit the weekly Mantra formazione. **Zero sockets.**

The network call (`apileague.teamLineup_read`) and the database session are both faked, so
the shell is exercised without Postgres or a token, in the socket-free default tier. The
pure formatter is tested directly; the command is a thin wrapper around it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from fantabot.domain.lineup.models import PlannedLineup
from fantabot.interface.app import app
from fantabot.interface.lineup import format_lineup, format_plan, is_past_deadline

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
            "teamLineupDto": {"mday": 1, "cmday": 3, "tid": 10000003},
            "lineUpInfo": _LINEUP_INFO,
        },
    )
    monkeypatch.setattr(
        apileague, "lineup_settings", lambda *a, **k: {"mods": ["343"], "tbench": 12}
    )
    # sroles=2 -> Mantra, the format these fixtures are in (marle-coded lineUpInfo).
    monkeypatch.setattr(apileague, "roster_settings", lambda *a, **k: {"sroles": 2})


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


def _submit_fakes(monkeypatch: pytest.MonkeyPatch, *, auto_act: bool) -> list[Any]:
    from fantabot import config
    from fantabot.adapters.http import apileague

    _fakes_plan(monkeypatch)
    monkeypatch.setattr(config.settings, "fantabot_auto_act", auto_act)
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
    from fantabot import config
    from fantabot.adapters.http import apileague
    from fantabot.domain.lineup.errors import LineupRejected

    _fakes_plan(monkeypatch)
    monkeypatch.setattr(
        apileague, "lineup_settings", lambda *a, **k: {"mods": ["442", "343"], "tbench": 12}
    )
    monkeypatch.setattr(config.settings, "fantabot_auto_act", True)
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
    from fantabot import config
    from fantabot.adapters.http import apileague

    _fakes_plan(monkeypatch)
    monkeypatch.setattr(config.settings, "fantabot_auto_act", True)
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
    from fantabot import config
    from fantabot.adapters.http import apileague
    from fantabot.domain.lineup.errors import LineupRejected

    _fakes_plan(monkeypatch)
    # a roster that fields more than one module, so there is a next-best to fall to
    monkeypatch.setattr(
        apileague, "lineup_settings", lambda *a, **k: {"mods": ["442", "343"], "tbench": 12}
    )
    monkeypatch.setattr(config.settings, "fantabot_auto_act", True)
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
