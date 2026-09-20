"""`POST /asta/room/bid` — the one route in this app that can spend credits.

**It does not bid, and that is the design.** It starts `fantabot asta bid` as a supervised
child and the *child* holds the locks: `FANTABOT_AUTO_ACT` is read inside `place_raise` at
write time, `--arm` is an argv token the child reads, and `domain/asta/bid.py::max_cap` is
computed and applied inside the loop, where this layer has no seam to reach it. The app's
whole job is to decide whether to pass `--arm` and to say which lock is shut when it will
not. Reimplementing any of the three here would be a second arming contract, and a second
one is one that disagrees.

**The MAX cap is the last line of defence and nothing in this package may name it.**
`docs/fantalab/01:142` calls the MAX client-enforced and `06:389-412` shows the RTDB rules
validating only that a raise exceeds the current price and names the right lot — nothing on
the server enforces it at all. `reservations` really does return the whole remaining budget
for a target whose removal makes the roster infeasible, which reads as "pay anything" with
28 slots still empty. `app/tests/test_fitness.py` scans for the name.

**Its own module, not `room.py`.** That file's `test_the_watch_cannot_arm` asserts `"--arm"
does not appear in its source — a scan that covers the request shape somebody adds an `arm`
field to tomorrow, and one that would be silently disarmed by putting this route beside it.
The read path and the one write path are worth keeping apart anyway.

**Every failure is in the body, not in the status.** The watch route raises a 400 because a
bad link is its only failure; this one can refuse for five reasons, and a route whose
failures are half HTTP status and half outcome is one the page has to handle twice. The one
exception is a missing `arm`, which is a 422 from the schema — see `BidRequest`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from fantabot_app.api.infrastructure import processes
from fantabot_app.api.infrastructure.jobs import registry
from fantabot_app.api.v1.endpoints.room import check_room, stored_connect

router = APIRouter()

#: The supervised child's kind, and the name the guard below counts. One spelling, because a
#: second would never match and the refusal would silently stop refusing — which looks
#: exactly like "nothing is running".
BID_KIND = "asta-bid"

#: What this surface says when a lock is shut. The *fact* is shared with the CLI
#: (`application.arming`); the wording is local, because there is no `--arm` flag in an HTTP
#: request and a message naming one sends the reader to a terminal they are not using.
APP_SENTENCES = {
    "arm": "the request did not ask to arm",
    "FANTABOT_AUTO_ACT": "FANTABOT_AUTO_ACT is false",
}


class BidRequest(BaseModel):
    """A room link and an arming intent. Nothing from the value model.

    **`arm` has no default.** Not "defaults to false" — *absent*, so a request that does not
    say is a 422. `application/arming`'s rule and the reason for it: the operator who armed
    it is the one watching, so the intent is restated on every request that could act, and a
    default either way is a decision the last request makes for the next one.

    No `--lam` and no alphas: those are the child's own option set, declared once in
    `interface/asta.py`. A copy here is the second value model `application/asta_planner.py`
    exists to prevent — three commands once held three, and `asta bid` planned on plain `fvm`
    for a week after `asta optimize` had stopped.
    """

    url: str
    arm: bool


class BidStarted(BaseModel):
    outcome: str
    reason: str = ""
    #: Empty on every outcome but `started`.
    job_id: str = ""
    armed: bool = False
    #: Every shut lock, **by name**, in the order an operator would fix them. Empty when
    #: armed. A list and not a sentence so the page can mark each control; `reason` is the
    #: same facts as one line, for a reader that has no controls to mark.
    closed: list[str] = []
    #: The roster band this run was started with, and where the number came from. Echoed
    #: rather than left implicit: the room check card already shows both, and until this
    #: route sent them the child planned against something else entirely — so the screen and
    #: the run agreed about the band only by coincidence.
    roster_size: int | None = None
    roster_provenance: str = ""


def bid_flag(fantaleague_id: str) -> Path:
    """The child's stop flag, derived the same way the child derives it.

    `room_stop_path` is `fantabot`'s, called with `config.journal_path()` on both sides, so
    there is one spelling of one fact and no `--stop-flag` token to disagree about. The role
    is `bid` and not `watch` because a watch and a bid on one room are the *intended*
    pairing — an operator watches, then arms — and one flag for both would let Stop on the
    watch end the bidding.
    """
    from fantabot.adapters.files.stopflag import room_stop_path
    from fantabot.config import journal_path

    # Annotated, not returned bare: `fantabot` ships no `py.typed`, so this venv's mypy reads
    # every symbol from it as `Any` — `room.watch_flag`'s reason for the same shape.
    flag: Path = room_stop_path(journal_path(), fantaleague_id, "bid")
    return flag


@router.post("/asta/room/bid", response_model=BidStarted, tags=["asta"])
def room_bid(request: BidRequest) -> BidStarted:
    """Resolve the room, then run `fantabot asta bid` against it as a supervised child.

    **The room is resolved first, and by the same call the check button uses.** Not to be
    tidy: `asta bid` is unauthenticated by design and cannot read its own shard, seat, uid,
    format or corpus shape — its defaults for the last two are `8 x 500`, which is a
    different lega's game. Planning against the wrong shape is not a slightly-off plan: with
    no corpus a Classic room bought its 25-man roster for 25 credits of 500.

    A room that does not resolve starts nothing and says which of the four reasons it was.
    """
    from fantabot.application.arming import decide_arming

    # **One bidder at a time, and refused across rooms rather than within one.** A
    # `ProcessJob` built with a flag and no landing zone takes no role lock — its own
    # docstring says "one job per flag, which is the caller's to keep" — and `registry.start`
    # de-duplicates nothing, so two clicks used to put two armed children on the same seat.
    # Each reads `purchases/<fl>` independently and computes its own `credits_left`, so on a
    # lot they both want they raise *each other* to their walk-away and the plan's budget
    # goes twice over on one lot. The shared flag is worse: `ProcessJob.start` clears it
    # before spawning, so the second child erases a stop the first has not read.
    #
    # Across rooms because `config.journal_path()` is a single file: two bidders interleave
    # their rows in the record the room view tails and the 2026-09-01 audit was done against.
    running = next(
        (job for job in registry.list() if job.kind == BID_KIND and job.status == "running"),
        None,
    )
    if running is not None:
        return BidStarted(
            outcome="refused",
            reason=(
                f"a bidding run is already going ({running.id}). Stop it before starting "
                "another: two bidders on one seat raise each other, and they share one "
                "journal and one stop flag."
            ),
        )

    room = check_room(request.url, connect=stored_connect)
    if room.outcome != "resolved":
        return BidStarted(outcome=room.outcome, reason=room.reason)

    # Every field the child cannot read for itself, and every one of them optional on
    # `RoomCheck` because the platform's own field is. `resolve_room` refuses an unknown
    # `asta_type`, a non-free raise mode and a seat we do not hold, and nothing guarded the
    # rest — so a room that declared no shard answered `started` over a child that died on
    # `--db None`, with the reason visible only in the job log.
    #
    # `is None`, never a truth test: FantaLab's shards are 0-indexed and `if not shard`
    # refuses the first one.
    missing = [
        name
        for name, value in (
            ("shard", room.shard),
            ("asta_type", room.asta_type),
            ("num_teams", room.num_teams),
            ("num_credits", room.num_credits),
            ("seat_team_id", room.seat_team_id),
            ("seat_user_id", room.seat_user_id),
            # The band sizes the plan and divides the MAX cap. `asta bid` cannot read it —
            # that is what `--size` is for — and `"--size None"` would reach the child as a
            # Click usage error, which the page draws as a healthy "Bidding · <id>".
            ("roster_size", room.roster_size),
        )
        if value is None
    ]
    if missing:
        return BidStarted(
            outcome="refused",
            reason=(
                f"the room declares no {', '.join(missing)}. `asta bid` is unauthenticated "
                "and cannot read any of these for itself, and every one of them has a "
                "default that is a different lega's game."
            ),
        )

    # The two locks the app can see. The third — a stale listone bridge — needs a fetch, so
    # it stays the child's and is reported in the job log by name: `room_arming` refuses
    # arming there, never the run, exactly as it does in a terminal.
    gate = decide_arming(arm=request.arm)

    argv = [
        "asta", "bid",
        "--league", str(room.fantaleague_id),
        "--db", str(room.shard),
        "--team", str(room.seat_team_id),
        "--user", str(room.seat_user_id),
        # Read from the room, never guessed. `asta bid` has no authenticated read to reach
        # any of these three, and every one of them has a default that is somebody else's
        # game: a wrong `--format` prices and caps against the wrong band.
        "--format", str(room.asta_type),
        "--teams", str(room.num_teams),
        "--credits", str(room.num_credits),
        "--budget", str(room.num_credits),
        # **And no `--lega`.** A FantaLab room is not a lega, and passing one would hand the
        # child a second, older opinion about the band to fall back on — which is the defect
        # this flag closes, not the fix. `--size` is the whole answer; `--format` is the
        # other half `_lega_rules` would have supplied.
        "--size", str(room.roster_size),
    ]
    if gate.armed:
        argv.append("--arm")

    job = processes.ProcessJob(
        processes.fantabot_command(*argv), flag=bid_flag(str(room.fantaleague_id))
    )
    return BidStarted(
        outcome="started",
        # `armed` travels with the job so a reloaded tab can still tell a live bidder
        # from a rehearsal. It is what the app decided, not what the child will do: the
        # child re-reads the ambient lock on every write and can still refuse.
        job_id=registry.start(job.run, kind=BID_KIND, stop=job.stop, armed=gate.armed),
        armed=gate.armed,
        closed=list(gate.closed),
        reason=gate.because(APP_SENTENCES),
        roster_size=room.roster_size,
        roster_provenance=room.roster_provenance,
    )
