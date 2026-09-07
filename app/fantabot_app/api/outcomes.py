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

#: `GET /asta/target-prices`. `no_data` is a real answer here rather than a failure: the
#: fit needs training seasons of `statistiche`, and a fresh install has none.
TARGET_PRICES_OUTCOMES = ("priced", "no_data", "unreachable")


def because(exc: Exception) -> str:
    """One line, typed. `endpoints/room.py`'s idiom, and `tests/conftest.py`'s before it.

    A driver traceback says the call failed; it does not say which of five things failed,
    and it is not something to put on a page. The type plus the first line is the most a
    reader can act on and the least that identifies the fault.
    """
    return f"{type(exc).__name__}: {str(exc).splitlines()[0]}"


__all__ = [
    "ASTA_PLAN_OUTCOMES",
    "LINEUP_PLAN_OUTCOMES",
    "TARGET_PRICES_OUTCOMES",
    "because",
]
