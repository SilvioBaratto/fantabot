"""`lineup plan` and `GET /lineup/plan`, and the one precondition they share.

This is the only surface that reads the live platform — `my_team` -> `teamLineup_read` ->
`lineup_settings`, over a bearer token — so a parity run with no token cannot compare two
lineups. What it *can* compare, and what matters more before anything is armed, is that
the two refuse for the same reason: a screen that says "no lineup" where the command says
"no encryption key" is the gap the operator cannot see, in its smallest form.

The tier opens no sockets on this path. It never gets far enough: both sides check for a
key and a token first, and the seeded database has neither.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from typer.testing import Result

from .conftest import SeededWorld


def _has_credentials() -> bool:
    """A key *and* a stored token for the seeded lega. Neither is in the tier's database."""
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings

    if not settings.fantabot_encryption_key:
        return False
    with database_manager.get_session() as session:
        return bool(TokenStore(session).status())


def test_both_sides_refuse_without_a_key_or_a_token(
    seeded_db: SeededWorld, frozen_today: object, api: TestClient, cli: Callable[..., Result]
) -> None:
    if _has_credentials():
        pytest.skip("the tier's database holds a token — this test is about the refusal")

    body = api.get("/api/v1/lineup/plan", params={"league_id": seeded_db.league_id}).json()

    assert body["found"] is False
    # A reason, not a bare false. `endpoints/room.py`'s five named outcomes are the model,
    # and 1.7 brings the other three routes onto it.
    assert body["reason"], "the page refused without saying why"

    # The command refuses too, and says something. Its exit code is 1 by design — a plan
    # that cannot be built is not a plan, and `--arm` is a separate lock beyond it.
    result = cli("lineup", "plan", "--league", str(seeded_db.league_id), expect_exit=1)
    assert result.output.strip(), "the command refused silently"


def test_the_lineup_command_never_reaches_the_submit_path(
    seeded_db: SeededWorld, cli: Callable[..., Result], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`lineup plan` is a printer. `teamLineup_submit` is `interface/lineup.py`'s one
    writing call and the T-spine ratchet's first entry; a plan that reached it would be a
    lineup submitted by a preview."""
    from fantabot.adapters.http import apileague

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("`lineup plan` called teamLineup_submit")

    monkeypatch.setattr(apileague, "teamLineup_submit", refuse)
    cli("lineup", "plan", "--league", str(seeded_db.league_id), expect_exit=1)
