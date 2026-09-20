"""What `asta live` asks for, as opposed to what it prints.

3.10 lifted the fold into `application/asta_advisory`; what stayed in the Typer body is the
translation from options to an `AdvisoryRequest`, and that is exactly the seam
`CLAUDE.md` records `GET /asta/plan` losing ten inputs at. The goldens cover the *output* of
one invocation with default options — they cannot see an option that stopped being
forwarded, because the golden was recorded without it.

Survivor 12 of 3.10's own battery is the case: replacing `sentiment=sentiment` with
`sentiment=True` left all 2,133 tests green. `--no-sentiment` is the ablation control — the
one switch whose whole purpose is to make the answer different — and nothing noticed it
being ignored.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner


def _run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *args: str
) -> dict[str, Any]:
    """Drive `asta live --replay` with the fold faked, and return the request it built.

    A real (empty) replay file, because `Path(replay).read_text()` is the Typer body's own
    and deliberately not faked: patching it would leave the one line that turns an operator's
    path into an argument untested.
    """
    import contextlib

    from fantabot.adapters.http.fantalab import listone
    from fantabot.adapters.persistence import database_manager
    from fantabot.interface import asta
    from fantabot.interface.app import app

    seen: dict[str, Any] = {}

    monkeypatch.setattr(listone, "fetch", lambda **_k: {"uuid-1": 7})
    monkeypatch.setattr(
        database_manager, "get_session", lambda: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(asta, "parse_replay_lines", lambda _lines: [])
    monkeypatch.setattr(asta, "normalize", lambda _rows: [])

    def fake_build(_session: Any, request: Any, **kwargs: Any) -> Any:
        seen["request"] = request
        seen["bridge"] = kwargs["bridge"]

        class _Advisory:
            dropped_sales = 0
            result = None

        return _Advisory()

    import fantabot.application.asta_advisory as advisory

    monkeypatch.setattr(advisory, "build_advisory", fake_build)

    replay = tmp_path / "recorded.jsonl"
    replay.write_text("", encoding="utf-8")
    result = CliRunner().invoke(
        app, ["asta", "live", "--replay", str(replay), "--team", "US", *args]
    )
    seen["result"] = result
    return seen


def test_the_ablation_control_reaches_the_fold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--no-sentiment` is the one switch whose whole purpose is a different answer, and
    the goldens cannot see it: they were recorded without it."""
    seen = _run(monkeypatch, tmp_path, "--no-sentiment")

    assert seen["result"].exit_code == 0, seen["result"].output
    assert seen["request"].sentiment is False


def test_sentiment_is_on_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half: a body hard-coding `False` would pass the test above alone."""
    assert _run(monkeypatch, tmp_path)["request"].sentiment is True


def test_the_pinned_run_is_parsed_and_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A date, never a string. Parsing raises `typer.BadParameter`, which cannot happen in
    `application/` — the translation is what stays in the Typer body."""
    seen = _run(monkeypatch, tmp_path, "--sentiment-run", "2026-08-28")

    assert seen["request"].sentiment_run == date(2026, 8, 28)


def test_every_number_the_operator_chose_reaches_the_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ten-input drift, as a test: a field that stops being forwarded fails here.

    Deliberately none of these is a default — a request built from the command's own
    fallbacks would satisfy an assertion written against them.
    """
    seen = _run(
        monkeypatch,
        tmp_path,
        "--budget", "650", "--lam", "0.9", "--tilt-k", "0.4",
        "--teams", "10", "--credits", "650", "--season", "2025/26",
    )
    request = seen["request"]

    assert request.our_team_id == "US"
    assert (request.budget, request.lam, request.tilt_k) == (650.0, 0.9, 0.4)
    assert (request.num_teams, request.num_credits) == (10, 650)
    assert request.season == "2025/26"


def test_the_bridge_is_fetched_here_because_the_two_event_sources_differ(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--replay` is developer machinery and CLI-only (`tasks/archive/parity-spec.md` T20), so `build_advisory`
    takes events rather than reading them — one fold over two sources. The bridge is the
    same either way and is handed over."""
    assert _run(monkeypatch, tmp_path)["bridge"] == {"uuid-1": 7}
