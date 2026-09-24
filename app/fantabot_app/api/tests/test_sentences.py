"""`api/sentences.py` — one arming vocabulary, keyed by the names the contract uses.

The two acting routes each carried their own copy of this table, identical in every
sentence: `endpoints/lineup.py` keyed with the imported `ARM`/`AUTO_ACT` constants and
`endpoints/room_bid.py` with the literals `"arm"` and `"FANTABOT_AUTO_ACT"`. Both comments
said the wording was local while the *fact* was shared — true of the CLI, and no longer
true of a second route on the same surface.

**The literal-keyed copy was a fault waiting on a rename, not a style difference.**
`Arming.because` looks each shut lock up by the name `application/arming` gave it, so
renaming `AUTO_ACT` there leaves the constant-keyed copy correct and makes the other raise
`KeyError` — inside the branch whose only job is to explain a refusal, on the route that
spends credits.

Collapsing the two tables does not by itself close that. The test below does: it asks
`decide_arming` for every lock it can actually put in `closed` and requires this table to
render each one, so a **third** lock added to the contract fails here rather than on the
next refusal. It is driven off `decide_arming`'s own output rather than off `ARM`/`AUTO_ACT`
— naming the constants here would be a third copy of the list and could not notice a lock
that is added without being exported.
"""

from __future__ import annotations

import itertools

from fantabot.application.arming import decide_arming

from fantabot_app.api.sentences import APP_SENTENCES


def _every_closable_lock() -> set[str]:
    """Every name `decide_arming` can put in `Arming.closed`, over its whole input space."""
    return {
        lock
        for arm, auto_act in itertools.product((True, False), repeat=2)
        for lock in decide_arming(arm=arm, auto_act=auto_act).closed
    }


def test_the_table_renders_every_lock_the_contract_can_shut() -> None:
    """A lock with no sentence is a `KeyError` on the refusal path. There is no partial
    credit here: `because` joins *all* the shut locks, so one missing name takes out the
    two-locks-shut case as well as its own.
    """
    missing = sorted(_every_closable_lock() - set(APP_SENTENCES))
    assert missing == [], f"`APP_SENTENCES` cannot name a shut lock: {missing}"


def test_the_table_names_nothing_the_contract_cannot_shut() -> None:
    """The other direction, and it is what catches a rename rather than an addition.

    A renamed lock leaves the old spelling behind as an entry nothing can ever look up —
    which is exactly the state `endpoints/room_bid.py` would have been left in, and which
    reads as a table that still covers the contract.
    """
    stale = sorted(set(APP_SENTENCES) - _every_closable_lock())
    assert stale == [], f"`APP_SENTENCES` names a lock the contract cannot shut: {stale}"


def test_both_acting_routes_read_this_table_and_not_a_copy() -> None:
    """The collapse itself, pinned by identity rather than by equality.

    Two dicts that are `==` today are the state this module was written to end. `is` is the
    property: a copy reintroduced beside either route fails here even while every sentence
    in it still matches.
    """
    from fantabot_app.api.v1.endpoints import lineup, room_bid

    assert lineup.APP_SENTENCES is APP_SENTENCES
    assert room_bid.APP_SENTENCES is APP_SENTENCES
