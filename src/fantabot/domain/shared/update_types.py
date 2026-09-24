"""FantaLab's ``update_type`` vocabulary, declared once. Pure -- no I/O, no clock.

Every state on the ``auction/<fl>`` node carries an ``update_type``, and two ``domain/``
packages read that same stream through it: ``domain/harvest`` folds a captured evening into
ladders and sales, and ``domain/asta/live`` replays a captured ``auction/`` snapshot stream
into ``AssignmentEvent``s. Both carried their own ``CLOSE = "close_auction"`` -- one node,
one token, two owners, and so a correction that can land in one of them.

The obvious repair is the wrong one: having ``domain/asta`` import ``domain/harvest`` is
legal under ``tests/test_layers.py`` but buys a cross-feature edge inside ``domain/`` for a
string, making the asta advisory depend on the harvest package. So the vocabulary lives
here, beside ``parsing``, ``club_names``, ``values`` and ``league``, and neither feature
depends on the other.

**Tokens only.** ``domain/harvest/turn.BIDDING`` -- which of these put a price on the board
-- stays with the fold that applies it and is built from these names, so no literal of this
vocabulary is left behind anywhere. The one exception is deliberate and is on the *write*
side: ``domain/asta/bid.py`` spells ``"update_type": "raise"`` into an RTDB payload, where
the string is part of a wire body rather than a state being classified.

Source: ``docs/fantalab/06-asta-write-path.md`` §9 lists everything the client can write;
of those, ``first_call``, ``raise``, ``close_auction`` and ``confirm`` were seen on the wire.
"""

from __future__ import annotations

#: Puts a player on the block and starts his ladder from nothing. A turn can begin twice
#: for the same player: an annulled call puts him back on the block from zero
#: (``domain/harvest/turn.starts_new_turn``).
FIRST_CALL = "first_call"

#: A bid. The only ``update_type`` a participant bot ever writes.
RAISE = "raise"

#: Closes a player's auction -- i.e. a sale.
#:
#: It is what a *recorded* evening is read through, by both readers here. It is **not**
#: what the live path keys off: a close is reversible (RIAPRI) and ASSEGNA lots settle on
#: a separate node sometimes without one at all, so a live room is read off the
#: ``purchases/<fl>`` ledger instead (``docs/fantalab/06-asta-write-path.md`` §10, and
#: ``domain/asta/live``'s own docstring).
CLOSE = "close_auction"
