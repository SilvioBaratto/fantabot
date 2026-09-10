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


def test_a_dry_run_from_the_browser_matches_the_command(
    seeded_db: SeededWorld, frozen_today: object, api: TestClient, cli: Callable[..., Result]
) -> None:
    """Checkpoint C's first criterion, on the path a tier database can actually reach.

    `fantabot lineup submit` (no `--arm`) and `POST /lineup/submit` (`arm: false`) both call
    `application/lineup_submit.submit_lineup`, so there is one implementation — but "one
    implementation" is what `_cli_plan` claimed before 1.14 found it comparing a copy. So
    both are run and their answers put side by side.

    **What is compared is the refusal**, because the tier database holds no usable
    credential: `submit_lineup` stops at the credential before it reaches the platform, which
    is the correct ordering (a credential problem must not be reported as a network failure)
    and is also the only half measurable without a live token. The happy path needs a lega
    whose token opens, and closing that gap is an operator action — `auth login` is
    interactive and headed on purpose.

    **The happy path has since been measured by hand**, against the operator's own lega
    4103937 on 2026-09-10 once `d206dd9` unblocked `auth login` and both leghe were re-authed:
    matchday 2, `FANTABOT_AUTO_ACT=false`, no `--arm`, nothing submitted, and the two surfaces
    agreed on module `3421`, the same eleven starters **in the same order**, the same twelve
    bench, and `submitted=false`. Lega 3584692 refused on both with a byte-identical reason.
    The refusal *sentence* differs between surfaces by design — `arming.py` shares the two
    locks by name and words them per surface — and both name both locks.

    None of that can be pinned here, which is why it is written down rather than asserted: it
    needs a stored token, and this tier has none.
    """
    body = api.post(
        "/api/v1/lineup/submit", json={"league_id": seeded_db.league_id, "arm": False}
    ).json()
    result = cli("lineup", "submit", "--league", str(seeded_db.league_id), expect_exit=1)

    # Neither acted, and neither could: the seeded lega has no token at all.
    assert body["submitted"] is False
    assert body["outcome"] in {"no_credential", "refused", "unreachable"}, body

    # The same fact, in the same words — **equal**, not one containing a prefix of the
    # other. This was `body["reason"].splitlines()[0][:60] in " ".join(...)`, which is
    # one-way containment of a truncated prefix and left three whole classes of divergence
    # green: the page shortening its refusal to any substring of the command's, the command
    # adding words the page does not have, and the command dropping everything after
    # character 60. The compared sentence here is 135 characters, of which 60 were checked.
    #
    # Whitespace is normalised on both sides and nothing else is: Rich hard-wraps to the
    # terminal width, so the line breaks are the renderer's and are not part of the fact,
    # while every word is.
    assert body["reason"], "the page refused without saying why"
    said_by_the_page = " ".join(body["reason"].split())
    said_by_the_command = " ".join(result.output.split())

    assert said_by_the_command == said_by_the_page, (
        f"the page says {said_by_the_page!r} and the command says {said_by_the_command!r}"
    )


def test_the_browser_cannot_arm_by_omission(
    seeded_db: SeededWorld, api: TestClient
) -> None:
    """The property a terminal gets for free and a browser does not.

    `--arm` is absent unless typed. A body field could be absent, `null`, or left over in a
    restored form, so the server requires it: a request that does not say is a 422, never a
    dry run and never an armed one.
    """
    response = api.post("/api/v1/lineup/submit", json={"league_id": seeded_db.league_id})

    assert response.status_code == 422
    assert ["body", "arm"] in [d["loc"] for d in response.json()["detail"]]
