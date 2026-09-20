"""`GET /asta/advisory` — the rolling advisory over a live room's sale ledger.

The read-only half of the asta path, and the one an operator falls back on when the room
view is gone: the target roster after every sale so far, a walk-away each, and what every
rival has left to spend with.

**It is unauthenticated, and that is the CLI's contract carried over rather than a
shortcut.** `asta live --league --db` needs no token — the `purchases/<fl>` ledger is on the
open RTDB (docs/fantalab/06 §10) — so this route takes the shard and our team id as the CLI
does instead of resolving the room. A route that resolved would need a FantaLab session for
a read that does not, and would answer `no_credential` to a question about a ledger.

**Five outcomes, one per cause.** `outcomes.py`'s rule and §3.4's defect: four different
failures wearing one label is not fixed by a better message. `except Exception -> found=False`
is what this is written against.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app

ROOM = "8ca35cbf-0f7a-4b3a-9e2e-3f2a1b0c4d5e"


def _advisory(**over: Any) -> Any:
    """A built `Advisory`, so these tests exercise the route and not the fold."""
    from fantabot.application.asta_advisory import (
        Advisory,
        AdvisoryOpponent,
        AdvisoryTarget,
    )
    from fantabot.application.plan_inputs import PlanInputs

    fields: dict[str, Any] = {
        "targets": (
            AdvisoryTarget(player_id="2", nome="Zaccagni", walk_away=44, chase=True),
            AdvisoryTarget(player_id="1", nome="Svilar", walk_away=0, chase=False),
        ),
        "opponents": (
            AdvisoryOpponent(team_id="THEM", players=3, spent=120, remaining=530),
        ),
        "sales": 4,
        "dropped_sales": 1,
        "total_cost": 412,
        "objective": 1897.0,
        "world": PlanInputs(
            pool=[object()], value=lambda _p: 1.0, prices={"2": 38.0}, teams={}, names={},
            roles={}, legality={}, sentiment=None,
        ),
    }
    fields.update(over)
    return Advisory(**fields)


@pytest.fixture
def wired(monkeypatch):
    """The ledger read and the fold, faked. What is asserted is the route's own choices."""
    from fantabot_app.api.v1.endpoints import asta as endpoint

    seen: dict[str, Any] = {}

    def fake_ledger(db: int, league: str) -> list[Any]:
        seen["ledger"] = (db, league)
        return ["an-event"]

    def fake_bridge(**_k: Any) -> dict[str, int]:
        return {"uuid-1": 7}

    def fake_build(_session: Any, request: Any, **kwargs: Any) -> Any:
        seen["request"] = request
        seen["events"] = kwargs["events"]
        seen["bridge"] = kwargs["bridge"]
        return _advisory()

    monkeypatch.setattr(endpoint, "ledger_events", fake_ledger, raising=False)
    monkeypatch.setattr(endpoint, "listone_fetch", fake_bridge, raising=False)
    monkeypatch.setattr(endpoint, "build_advisory", fake_build, raising=False)
    return seen


def _get(client: TestClient, **params: Any):
    query = {"league": ROOM, "db": 3, "team": "TEAM-7", "credits": 650, "teams": 10, **params}
    return client.get("/api/v1/asta/advisory", params=query)


class TestTheAdvisoryIsRendered:
    def test_it_reads_the_ledger_for_the_shard_and_room_it_was_given(self, wired) -> None:
        assert _get(TestClient(app)).status_code == 200
        assert wired["ledger"] == (3, ROOM)

    def test_the_rooms_own_shape_reaches_the_fold(self, wired) -> None:
        """An 8x500 default against a 10x650 room prices every target against another
        lega's game — the same defect `GET /asta/plan` carries `teams`/`credits` for."""
        _get(TestClient(app))

        assert (wired["request"].num_teams, wired["request"].num_credits) == (10, 650)
        assert wired["request"].our_team_id == "TEAM-7"

    def test_targets_carry_their_walk_away_and_whether_it_is_a_chase(self, wired) -> None:
        """A walk-away under one credit is not an instruction to bid. `reservations` clamps
        a negative marginal to zero — he is freely replaceable — and the bidder refuses at
        every price, so a screen saying "chase, walk-away 0" names the one thing the system
        will not do. He stays on the list: he is in the target roster."""
        body = _get(TestClient(app)).json()

        assert body["outcome"] == "advised"
        assert [t["nome"] for t in body["targets"]] == ["Zaccagni", "Svilar"]
        assert body["targets"][0]["chase"] is True
        assert body["targets"][1]["chase"] is False and body["targets"][1]["walk_away"] == 0

    def test_every_rival_carries_what_it_has_left(self, wired) -> None:
        body = _get(TestClient(app)).json()

        assert body["opponents"] == [
            {"team_id": "THEM", "players": 3, "spent": 120, "remaining": 530}
        ]

    def test_the_dropped_sales_are_counted_on_the_response(self, wired) -> None:
        """Each is a purchase nobody subtracted, so a rival's budget and that player's
        availability are both wrong until it is explained. Counted, never silent."""
        body = _get(TestClient(app)).json()

        assert body["sales"] == 4 and body["dropped_sales"] == 1


class TestEachFailureIsItsOwnName:
    @pytest.mark.parametrize(
        ("exc", "outcome"),
        [
            ("NoSentimentRows", "no_sentiment"),
            ("NoCorpus", "no_corpus"),
            ("EmptyPool", "empty_pool"),
            ("InfeasibleRoster", "infeasible"),
        ],
    )
    def test_a_refusal_is_named_rather_than_rendered_as_no_advisory(
        self, wired, monkeypatch, exc: str, outcome: str
    ) -> None:
        from fantabot.application.plan_request import EmptyPool, NoSentimentRows
        from fantabot.domain.asta.optimizer import InfeasibleRoster
        from fantabot.domain.asta.prices import NoCorpus

        from fantabot_app.api.v1.endpoints import asta as endpoint

        raises = {
            "NoSentimentRows": NoSentimentRows("no readings in the database"),
            "NoCorpus": NoCorpus("10x650 mantra", ("8x500",)),
            "EmptyPool": EmptyPool("no players for season 2026/27"),
            "InfeasibleRoster": InfeasibleRoster("no legal eleven"),
        }[exc]

        def boom(*_a: Any, **_k: Any) -> Any:
            raise raises

        monkeypatch.setattr(endpoint, "build_advisory", boom, raising=False)
        body = _get(TestClient(app)).json()

        assert body["outcome"] == outcome
        assert body["reason"], "a named outcome with no reason is a label, not an answer"
        assert body["targets"] == []

    def test_a_ledger_that_will_not_answer_is_unreachable_not_an_empty_advisory(
        self, wired, monkeypatch
    ) -> None:
        """An outage rendered as "no targets" is a false statement, not a missing one — and
        it is the state in which an operator decides they have nothing to chase."""
        from fantabot_app.api.v1.endpoints import asta as endpoint

        def boom(*_a: Any, **_k: Any) -> Any:
            raise OSError("connection reset")

        monkeypatch.setattr(endpoint, "ledger_events", boom, raising=False)
        body = _get(TestClient(app)).json()

        assert body["outcome"] == "unreachable" and "OSError" in body["reason"]

    def test_the_outcomes_are_pinned(self) -> None:
        """Four different failures wearing one label is not fixed by a better message —
        it is fixed by the outcomes ceasing to be the same value."""
        from fantabot_app.api.outcomes import ASTA_ADVISORY_OUTCOMES

        assert ASTA_ADVISORY_OUTCOMES == (
            "advised", "no_sentiment", "no_corpus", "empty_pool", "infeasible", "unreachable",
        )
