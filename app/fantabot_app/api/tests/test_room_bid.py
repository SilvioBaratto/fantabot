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


@pytest.fixture(autouse=True)
def no_bid_left_running():
    """Let each test start from "nothing is bidding".

    `registry` is process-wide and outlives every test in this module, and since the
    one-bidder guard landed that is not book-keeping: a child still running from the
    previous test makes the next request a legitimate `refused`, and the failure reads as
    the route being broken. The children here are a `print` and an exit, so this costs
    milliseconds. `test_room_watch.py` records the same fact about the registry without
    needing to act on it.
    """
    yield
    client = TestClient(app)
    _wait(
        lambda: not [
            job
            for job in client.get("/api/v1/jobs").json()["jobs"]
            if job["kind"] == "asta-bid" and job["status"] == "running"
        ]
    )


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


class TestItWillNotStartASecond:
    """**One bidder at a time, and the journal is the reason as much as the flag is.**

    `ProcessJob` built with a `flag` and no landing zone takes no role lock — its own
    docstring says *"one job per flag, which is the caller's to keep"* — and `registry.start`
    de-duplicates nothing. So two POSTs used to start two armed `asta bid` children on the
    same seat in the same room. Each reads `purchases/<fl>` independently and computes its
    own `credits_left`, so on a lot they both want they raise **each other** to their
    walk-away and the plan's budget goes twice over on one lot.

    The shared flag is the sharper half: `ProcessJob.start` calls `clear_stop` on
    `room-<id>.bid.stop` before spawning, so starting the second child **erases a stop
    request the first has not read yet**, and either job's Stop then reaches both.

    Refused across rooms, not only within one, and `config.journal_path()` is why: it is a
    single file, so two bidders interleave their rows in the record the room view tails and
    the 2026-09-01 audit was done against.
    """

    def test_a_second_bid_is_refused_while_one_is_running(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)

        first = _bid(client, arm=True).json()
        assert first["outcome"] == "started"

        second = _bid(client, arm=True).json()

        assert second["outcome"] == "refused"
        assert first["job_id"] in second["reason"], (
            "a refusal that does not name the run already going is one an operator cannot act on"
        )
        assert second["job_id"] == ""
        assert second["armed"] is False, "a refused request did not arm anything"

    def test_and_the_second_request_starts_no_child(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)
        _bid(client, arm=True)
        before = len(client.get("/api/v1/jobs").json()["jobs"])

        _bid(client, arm=True)

        assert len(client.get("/api/v1/jobs").json()["jobs"]) == before

    def test_a_finished_run_does_not_block_the_next_one(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        """The guard is about a *running* child. A refusal that outlived the run it named
        would make the page unusable for the rest of the session."""
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)
        first = _bid(client, arm=True).json()["job_id"]
        assert _wait(lambda: _job(client, first)["status"] == "done")

        assert _bid(client, arm=True).json()["outcome"] == "started"


class TestItRefusesARoomItCannotDescribe:
    """A field the room did not declare must not reach argv as the string `"None"`.

    `RoomCheck` types every one of these as `X | None` and `resolve_room` refuses only on an
    unknown `asta_type`, a non-free raise mode and a seat we do not hold. Nothing guarded the
    rest, so the route answered `started` over a child that died instantly on a Typer parse
    error — `--db None --teams None --credits None` — and the page drew "Bidding · <id>"
    with the reason visible only in the job log.
    """

    @pytest.mark.parametrize(
        "missing", ["shard", "asta_type", "num_teams", "num_credits", "seat_team_id",
                    "seat_user_id", "roster_size"],
    )
    def test_a_missing_field_is_refused_by_name_before_anything_spawns(
        self, quick_child, monkeypatch, missing: str
    ) -> None:
        from fantabot_app.api.v1.endpoints import room_bid
        from fantabot_app.api.v1.endpoints.room import RoomCheck

        def _check(url: str, *, connect: Any) -> RoomCheck:
            fields: dict[str, Any] = {
                "outcome": "resolved", "fantaleague_id": ROOM, "shard": 3,
                "asta_type": "classic", "num_teams": 10, "num_credits": 650,
                "seat_team_id": "TEAM-7", "seat_user_id": "USER-9", "roster_size": 25,
            }
            fields[missing] = None
            return RoomCheck(**fields)

        monkeypatch.setattr(room_bid, "check_room", _check)
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)
        before = len(client.get("/api/v1/jobs").json()["jobs"])

        body = _bid(client, arm=True).json()

        assert body["outcome"] == "refused"
        assert missing in body["reason"], (
            f"the refusal does not say which field was missing: {body['reason']!r}"
        )
        assert body["job_id"] == ""
        assert len(client.get("/api/v1/jobs").json()["jobs"]) == before

    def test_shard_zero_is_a_shard_and_not_a_missing_field(
        self, quick_child, monkeypatch
    ) -> None:
        """FantaLab's shards are 0-indexed, so a truth test refuses the first one."""
        from fantabot_app.api.v1.endpoints import room_bid
        from fantabot_app.api.v1.endpoints.room import RoomCheck

        monkeypatch.setattr(
            room_bid, "check_room",
            lambda *_a, **_k: RoomCheck(
                outcome="resolved", fantaleague_id=ROOM, shard=0, asta_type="mantra",
                num_teams=8, num_credits=500, seat_team_id="T", seat_user_id="U",
                roster_size=30,
            ),
        )
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)

        body = _bid(client, arm=True).json()

        assert body["outcome"] == "started"
        assert "--db 0" in _argv(client, body["job_id"])


class TestTheRoomsOwnBandReachesTheChild:
    """The band the room check already resolved, sent instead of thrown away.

    `check_room` calls `rules_for_room` and returns `roster_size` + `roster_provenance`, and
    the room check card on the Asta page **renders both** — so the operator reads "25 read
    from the room" and then clicks Bid on a child that plans and caps against something else.
    Without `--size` that something else is `--lega` → `settings.fantabot_league_id`, a
    *leghe.fantacalcio* league with no relation to the FantaLab room.

    Measured on this machine: that lega's last sync declares **32**. So a room declaring 25
    was planned as a 32-man roster — unbuyable, the `1.14` failure — and a room declaring 32
    while the lega said 25 would have capped **7 credits too loose**, which is the direction
    that costs money.
    """

    def test_the_argv_carries_the_size_the_room_declared(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)

        argv = _argv(client, _bid(client, arm=True).json()["job_id"])

        assert "--size 25" in argv, argv

    def test_it_sends_no_lega_so_the_band_cannot_come_from_one(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        """A FantaLab room is not a lega. Passing one would give the child a second, older
        opinion about the band to fall back on — which is the defect, not the fix."""
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)

        argv = _argv(client, _bid(client, arm=True).json()["job_id"])

        assert "--lega" not in argv

    def test_a_band_nobody_declared_is_still_sent_and_said_out_loud(
        self, quick_child, monkeypatch
    ) -> None:
        """`ASSUMED_NOTHING` is the common case — the `rules_for_room` docstring measures
        153 of 247 rooms declaring nothing — so refusing it would refuse most rooms. The
        assumed size is still strictly better than another league's real one, and the
        response says which it is rather than leaving the operator to assume."""
        from fantabot_app.api.v1.endpoints import room_bid
        from fantabot_app.api.v1.endpoints.room import RoomCheck

        monkeypatch.setattr(
            room_bid, "check_room",
            lambda *_a, **_k: RoomCheck(
                outcome="resolved", fantaleague_id=ROOM, shard=3, asta_type="mantra",
                num_teams=8, num_credits=500, seat_team_id="T", seat_user_id="U",
                roster_size=30, roster_provenance="assumed — nothing was declared",
            ),
        )
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)

        body = _bid(client, arm=True).json()

        assert body["outcome"] == "started"
        assert "--size 30" in _argv(client, body["job_id"])
        assert "assumed" in body["roster_provenance"]

    def test_a_declared_band_says_so_too(
        self, quick_child, resolved_room, monkeypatch
    ) -> None:
        _armed(monkeypatch, auto_act=True)

        body = _bid(TestClient(app), arm=True).json()

        assert body["roster_size"] == 25
        assert body["roster_provenance"] == "read from the room"

    def test_a_room_with_no_size_at_all_is_refused_by_name(
        self, quick_child, monkeypatch
    ) -> None:
        """`roster_size` is `int | None` on `RoomCheck`. `"--size None"` would reach the
        child as a Click usage error, which the page draws as "Bidding · <id>"."""
        from fantabot_app.api.v1.endpoints import room_bid
        from fantabot_app.api.v1.endpoints.room import RoomCheck

        monkeypatch.setattr(
            room_bid, "check_room",
            lambda *_a, **_k: RoomCheck(
                outcome="resolved", fantaleague_id=ROOM, shard=3, asta_type="mantra",
                num_teams=8, num_credits=500, seat_team_id="T", seat_user_id="U",
                roster_size=None,
            ),
        )
        _armed(monkeypatch, auto_act=True)
        client = TestClient(app)
        before = len(client.get("/api/v1/jobs").json()["jobs"])

        body = _bid(client, arm=True).json()

        assert body["outcome"] == "refused" and "roster_size" in body["reason"]
        assert len(client.get("/api/v1/jobs").json()["jobs"]) == before


def test_the_pin_covers_everything_the_room_check_can_say() -> None:
    """This route **forwards** `check_room`'s outcome rather than naming four of its own —
    one resolution path, one set of reasons, which is why they do not drift.

    The cost is that `test_outcomes.py`'s literal scan sees only `started` here, so the
    invariant is asserted where the two tuples can be compared: whatever the room check can
    answer, this route can forward, and `resolved` becomes `started` because by then
    something has been started.

    It fails the day `check_room` grows a sixth outcome — which would otherwise reach the
    page as a name no branch handles.
    """
    from fantabot_app.api.outcomes import ROOM_BID_OUTCOMES
    from fantabot_app.api.v1.endpoints.room import OUTCOMES

    assert set(ROOM_BID_OUTCOMES) == {"started"} | (set(OUTCOMES) - {"resolved"})
    assert "resolved" not in ROOM_BID_OUTCOMES, (
        "a room that resolved is not an outcome of this route: something was started"
    )


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
