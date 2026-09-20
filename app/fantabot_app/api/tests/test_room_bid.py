"""`POST /asta/room/bid` — the one route in this app that can spend credits.

It does not bid. It starts `fantabot asta bid` as a supervised child and **the child holds
the locks**: `FANTABOT_AUTO_ACT` is read inside `place_raise` at write time, `--arm` is an
argv token the child reads, and `domain/asta/bid.py::max_cap` is the last line of defence
behind both — `docs/fantalab/01:142` calls the MAX client-enforced and `06:389-412` shows the
RTDB rules validating only that a raise exceeds the current price and names the right lot.
Nothing on the server enforces it. So the app's whole job here is to decide *whether* to pass
`--arm` and to say which lock is shut when it will not.

**Three things are asserted that a route review would not catch.**

* The argv carries the room's **own** shape — shard, seat, format, teams, credits — read from
  the platform, never defaulted. `asta bid` is unauthenticated and cannot read any of them;
  its `--teams 8 --credits 500` fallback is a different lega's game, and planning against the
  wrong shape is how a Classic room bought a 25-man roster for 25 credits of 500.
* It carries **none of the value model's numbers**. `--lam` and the three alphas are the
  child's own option set; a copy here is the second value model `application/asta_planner.py`
  exists to prevent.
* `arm` is a body field with **no default**, so a request that does not say is a 422 and not
  a dry run — `application/arming`'s rule, and the reason it is a rule: the operator who
  armed it is the one watching, so the intent is restated on every request that could act.
"""

from __future__ import annotations

import sys
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app

ROOM = "8ca35cbf-0f7a-4b3a-9e2e-3f2a1b0c4d5e"


@pytest.fixture
def quick_child(monkeypatch, tmp_path):
    """The supervisor pointed at a short-lived real child that echoes its argv.

    `test_room_watch.py`'s fixture, for its reason: a fake `Popen` would agree with whatever
    the implementation happened to do.
    """
    from fantabot_app.api.infrastructure import processes

    monkeypatch.setenv("FANTABOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        processes,
        "fantabot_command",
        lambda *args: [sys.executable, "-c", f"print({' '.join(args)!r}, flush=True)"],
    )
    return tmp_path


@pytest.fixture
def resolved_room(monkeypatch):
    """A room that resolves, with a shape deliberately unlike every default in sight.

    10 teams and 650 credits, not 8x500; a Classic room, not Mantra. Every one of those is a
    value `asta bid` would otherwise have guessed, so an argv built from defaults cannot pass
    the assertions below by coincidence.
    """
    from fantabot_app.api.v1.endpoints import room_bid

    def _check(url: str, *, connect: Any) -> Any:
        from fantabot.domain.asta.live import parse_room_url

        from fantabot_app.api.v1.endpoints.room import RoomCheck

        return RoomCheck(
            outcome="resolved",
            fantaleague_id=parse_room_url(url),
            shard=3,
            asta_type="classic",
            asta_mode="CHIAMA",
            raise_mode="LIBERO",
            num_teams=10,
            num_credits=650,
            seat_team_id="TEAM-7",
            seat_team_name="Legamiallerotaie2",
            seat_user_id="USER-9",
            roster_size=25,
            roster_provenance="read from the room",
        )

    monkeypatch.setattr(room_bid, "check_room", _check)
    monkeypatch.setattr(room_bid, "stored_connect", lambda: ("USER-9", lambda _p: {}))


def _job(client: TestClient, job_id: str) -> dict:
    return client.get(f"/api/v1/jobs/{job_id}").json()


def _wait(done, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(0.05)
    return False


def _bid(client: TestClient, **body: Any):
    return client.post("/api/v1/asta/room/bid", json={"url": ROOM, **body})


def _argv(client: TestClient, job_id: str) -> str:
    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    return " ".join(_job(client, job_id)["lines"])


def _armed(monkeypatch, *, auto_act: bool) -> None:
    from fantabot import config

    monkeypatch.setattr(config, "_DOTENV_INJECTED", {})
    monkeypatch.setenv(config.AUTO_ACT_VAR, "true" if auto_act else "false")


class TestTheArmingContract:
    def test_a_request_that_does_not_say_is_a_422_not_a_dry_run(
        self, quick_child, resolved_room
    ) -> None:
        """`application/arming`'s own rule. A default — either way — is a decision the last
        request makes for the next one."""
        assert TestClient(app).post("/api/v1/asta/room/bid", json={"url": ROOM}).status_code == 422

    def test_both_locks_open_sends_the_flag(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)

        body = _bid(client, arm=True).json()

        assert body["outcome"] == "started"
        assert body["armed"] is True and body["closed"] == []
        assert "--arm" in _argv(client, body["job_id"])

    def test_a_request_that_does_not_ask_still_starts_and_names_the_lock(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        """A dry run is not a failure: it is what was asked for, and it is the rehearsal an
        operator does before arming. The run starts, watches, decides and sends nothing."""
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)

        body = _bid(client, arm=False).json()

        assert body["outcome"] == "started"
        assert body["armed"] is False
        assert body["closed"] == ["arm"]
        assert "--arm" not in _argv(client, body["job_id"])

    def test_the_ambient_lock_alone_shuts_it_and_says_so(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        _armed(monkeypatch, auto_act=False)
        client = TestClient(app)

        body = _bid(client, arm=True).json()

        assert body["armed"] is False and body["closed"] == ["FANTABOT_AUTO_ACT"]
        assert "FANTABOT_AUTO_ACT" in body["reason"]
        assert "--arm" not in _argv(client, body["job_id"])

    def test_both_shut_names_both(self, quick_child, resolved_room, monkeypatch) -> None:
        """The defect `application/arming` was written for: a ternary over two causes sends
        an operator to fix one, retry, and be told about the other."""
        _armed(monkeypatch, auto_act=False)

        body = _bid(TestClient(app), arm=False).json()

        assert body["closed"] == ["FANTABOT_AUTO_ACT", "arm"]
        assert "FANTABOT_AUTO_ACT" in body["reason"] and "arm" in body["reason"]


class TestTheChildIsToldTheRoomsOwnShape:
    def test_the_argv_carries_what_only_an_authenticated_read_could_know(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        """`asta bid` is unauthenticated by design: shard, seat, uid, format, teams and
        credits are exactly the fields it cannot read for itself, and every one of them has a
        default that is a different lega's game."""
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)

        argv = _argv(client, _bid(client, arm=True).json()["job_id"])

        for token in (
            f"--league {ROOM}", "--db 3", "--team TEAM-7", "--user USER-9",
            "--format classic", "--teams 10", "--credits 650", "--budget 650",
        ):
            assert token in argv, f"missing {token!r} from: {argv}"

    def test_it_names_none_of_the_value_models_numbers(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)

        argv = _argv(client, _bid(client, arm=True).json()["job_id"])
        named = [
            flag
            for flag in ("--lam", "--ceiling-alpha", "--bargain-beta", "--bargain-share",
                         "--tilt-k", "--no-sentiment")
            if flag in argv
        ]
        assert named == [], f"the app declares the value model's numbers: {named}"

    def test_the_job_is_its_own_kind_and_is_stoppable(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        """`asta-bid`, not `asta-watch`: the page renders a different banner over a run that
        can spend credits, and `GET /jobs` is the only thing a reopened tab has to tell them
        apart."""
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)

        job_id = _bid(client, arm=True).json()["job_id"]

        assert _wait(lambda: _job(client, job_id)["status"] == "done")
        row = next(r for r in client.get("/api/v1/jobs").json()["jobs"] if r["id"] == job_id)
        assert row["kind"] == "asta-bid" and row["stoppable"] is True

    def test_the_stop_flag_is_the_bid_one_not_the_watch_one(self, quick_child) -> None:
        """A watch and a bid on one room is the intended pairing — an operator watches, then
        arms. One flag for both would let Stop on the watch end the bidding."""
        from fantabot.adapters.files.stopflag import room_stop_path
        from fantabot.config import journal_path

        from fantabot_app.api.v1.endpoints.room import watch_flag
        from fantabot_app.api.v1.endpoints.room_bid import bid_flag

        assert bid_flag(ROOM) == room_stop_path(journal_path(), ROOM, "bid")
        assert bid_flag(ROOM) != watch_flag(ROOM)


class TestItRefusesBeforeItSpawns:
    @pytest.mark.parametrize(
        ("outcome", "reason"),
        [("bad_link", "not a room link"), ("no_credential", "nothing stored"),
         ("refused", "the room said no"), ("unreachable", "RuntimeError: boom")],
    )
    def test_a_room_that_does_not_resolve_starts_nothing(
        self, quick_child, monkeypatch, outcome: str, reason: str
    ) -> None:
        """Five outcomes, one per cause. `outcomes.py`'s rule and §3.4's defect: four
        different failures wearing one label is not fixed by a better message."""
        from fantabot_app.api.v1.endpoints import room_bid
        from fantabot_app.api.v1.endpoints.room import RoomCheck

        monkeypatch.setattr(
            room_bid, "check_room", lambda *_a, **_k: RoomCheck(outcome=outcome, reason=reason)
        )
        client = TestClient(app)
        before = len(client.get("/api/v1/jobs").json()["jobs"])

        body = _bid(client, arm=True).json()

        assert body["outcome"] == outcome and reason in body["reason"]
        assert body["job_id"] == ""
        assert len(client.get("/api/v1/jobs").json()["jobs"]) == before
