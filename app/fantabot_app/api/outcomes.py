"""Why a decision route said no — as a name, not as a false.

**The rule, written down because it was not.**

> Degrade **open** on a status read. Fail **closed** on a decision.

A status read answers "what is stored / connected / running": an empty answer is a true
answer, and a page that shows nothing is showing the truth. A *decision* route answers
"what should I buy" or "who should I field". There, `found=false` is not an answer at all —
it is five different answers wearing one label, and the operator's next move is different
for each.

What that cost, measured. `except Exception -> found=False` sat on `/asta/plan`,
`/lineup/plan` and `/asta/target-prices`. A database that would not open, a season nobody
had scraped, a roster the optimizer could not seed, a lega that had never been synced and a
corpus shape with no recorded sales all rendered as **"No plan yet"**, under a suggestion to
sync the lega — which is the right remedy for one of them and a waste of an evening for the
other four.

`endpoints/room.py` is the model and got there first: five outcomes, each with its own
reason and its own remedy, pinned as a tuple *because a better message would not have been
enough* — the outcomes had to stop being the same value. Two things it learned that apply
here:

* **The ordering is load-bearing.** A bad link must never report a decryption failure. The
  first live probe answered "not a link" with a `TokenUndecryptable`, which was a true
  statement about the credential and a useless answer to the question asked. So the checks
  run in the order the operator would ask them.
* **A remedy, not just a cause.** `no_credential` rather than `no_session`, because
  "nothing stored" and "stored under another key" are both "the credential is not usable"
  and both need something done by hand.

**No route catches bare `Exception`.** Each names the families it can actually fail on —
`SQLAlchemyError` for the database, `TokenError` and its subclasses for the platform, the
planner's own refusals — and anything else reaches FastAPI as a 500. That is deliberate and
it is what *fail closed on a decision* means at the limit: a 500 is logged, alarming and
unmistakably a fault, where a bare handler turns an unanticipated bug into a tidy page
saying "we could not ask". An earlier version allowed one bare handler per route as the
`unreachable` outcome; the criterion said none, and the criterion is right — the set of
things that can go wrong here is small enough to name.

**Pinned as tuples, and compared for exact equality by the tests.** A route that gains an
outcome must say so; a route that loses one must delete its name. That is the same ratchet
`tests/test_layers.py` keeps, for the same reason: a set that only ever grows stops meaning
anything.
"""

from __future__ import annotations

#: `GET /asta/plan`. Seven, and every one of them used to be `found=false`.
#:
#: `no_lega` is the one that is easy to miss: with no snapshot the endpoint does not fail,
#: it *guesses* — Mantra, 500 credits, and the hardcoded roster band — because the format
#: is derived from `role_groups` and the budget from the snapshot. A plan built on three
#: guesses is not a plan, and "sync the lega" is a remedy the operator can act on in a
#: minute.
ASTA_PLAN_OUTCOMES = (
    "planned",
    "no_lega",
    "no_sentiment",
    "no_corpus",
    "empty_pool",
    "infeasible",
    "unreachable",
)

#: `GET /lineup/plan`. The only decision route that reads the live platform, so its
#: failures are mostly about the credential and the network — and `refused` is the platform
#: itself saying no, which is a different fact from being unable to ask.
LINEUP_PLAN_OUTCOMES = (
    "planned",
    "no_credential",
    "no_lineup",
    "refused",
    "unreachable",
)

#: `GET /asta/advisory` — the rolling advisory over a live room's sale ledger.
#:
#: Five of the six are `ASTA_PLAN_OUTCOMES`', and for the same reasons: the advisory is a
#: plan re-solved after every sale, so it fails where a plan fails. `no_lega` is absent
#: because this route is given the room rather than a lega, and a ledger that will not answer
#: is `unreachable` — which a route rendering it as "no targets" would turn into a false
#: statement rather than a missing one, at the moment an operator decides they have nothing
#: to chase.
ASTA_ADVISORY_OUTCOMES = (
    "advised",
    "no_sentiment",
    "no_corpus",
    "empty_pool",
    "infeasible",
    "unreachable",
)

#: `GET /lineup/current` — the lineup the platform has saved right now.
#:
#: `LINEUP_PLAN_OUTCOMES`' five with `planned` becoming `read`, because the two routes ask
#: different questions of the same credential: one asks what we *should* field, this asks
#: what is *saved*. `no_lineup` means the competition has never had one set, which is the
#: ordinary state before a matchday's first submit — not an empty XI, which would be a claim
#: about the roster rather than about the save.
LINEUP_CURRENT_OUTCOMES = (
    "read",
    "no_lineup",
    "no_credential",
    "refused",
    "unreachable",
)

#: `POST /asta/room/bid` — the one route that can start a run which spends credits.
#:
#: Four of the five are `check_room`'s own, reached through the same call rather than
#: re-derived: a second resolution path is a second set of reasons, and they drift.
#: `started` covers both an armed run and a dry one, because a dry run is not a failure —
#: it is the rehearsal an operator does before arming, and it still watches, decides and
#: journals. Whether it may act is `armed`/`closed` on the body, not an outcome: a lock
#: being shut is a fact about the run, not about whether it began.
ROOM_BID_OUTCOMES = (
    "started",
    "refused",
    "bad_link",
    "no_credential",
    "unreachable",
)

#: `GET /asta/target-prices`. `no_data` is a real answer here rather than a failure: the fit
#: needs training seasons of `statistiche`, and a fresh install has none. `unknown_system` is
#: separate from it because the remedies differ — fix the spelling, or scrape a season — and
#: one screen over two remedies is the defect this module exists for. `system` reaches a
#: `WHERE listone = :system`, so an unrecognised value selected no rows and read as "no data".
TARGET_PRICES_OUTCOMES = ("priced", "no_data", "unknown_system", "unreachable")


def because(exc: Exception) -> str:
    """One line, typed. `endpoints/room.py`'s idiom, and `tests/conftest.py`'s before it —
    room.py held a second copy until 2026-09-24 and now calls this one.

    A driver traceback says the call failed; it does not say which of five things failed,
    and it is not something to put on a page. The type plus the first line is the most a
    reader can act on and the least that identifies the fault.

    **An exception with no message is the ordinary case here, not the exotic one.** `str()`
    of a bare `OSError`, `TimeoutError` or `ConnectionResetError`, of `SQLAlchemyError("")`
    and of `httpx.ConnectError("")` is `""` — measured, this venv, 2026-09-24 — and
    `"".splitlines()` is `[]`, so the unguarded `[0]` raised `IndexError` *inside* the
    `except` clause that exists to keep a fault off the 500 path. The families it fired on
    are exactly the ones these routes name: a socket that times out, a database handle that
    will not open, a transport error carrying its cause's empty string.
    Both copies carried it, which is why there is now one.

    The type alone is returned in that case, deliberately: `"OSError: "` is a colon
    promising a reason that is not there, and the name is the whole of what is known. That
    is not a fresh call — `interface/harvest.py::_constraint_of` is the one copy of this
    idiom that already carried the guard, and it renders the bare type for the same reason.
    The first line is stripped for the same rule's sake: a message that is only whitespace
    says nothing, so it reads as nothing rather than as a colon and three spaces.
    """
    first = (str(exc).splitlines() or [""])[0].strip()
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


__all__ = [
    "ASTA_ADVISORY_OUTCOMES",
    "ASTA_PLAN_OUTCOMES",
    "LINEUP_CURRENT_OUTCOMES",
    "LINEUP_PLAN_OUTCOMES",
    "ROOM_BID_OUTCOMES",
    "TARGET_PRICES_OUTCOMES",
    "because",
]
