"""Folding SSE frames into the merged state the rest of the pipeline consumes.

This is the seam between the two collection paths. The poller wrote merged
states directly; the live path arrives at the same shape here, so ``reconstruct``
needs no second implementation and the recorded evening keeps working as a
regression test for both.

**A frame's ``path`` is consulted, not assumed.** ``auction/<id>`` is a flat node
and every observed frame targets ``"/"``, but the field was parsed and then read
by nothing — so a frame aimed at a child key was applied at the root, and a
nested ``put`` wiped the whole auction. The spec's own Code Style snippet refuses
a non-root path; that guard was specified and not implemented. An unhandled path
now leaves the state alone.

⚠ **It is refused in silence.** `apply_frame` once took a `seen=` counter and
`unsupported_paths()` built one, so a caller could surface the refusals — but the
one caller, `adapters/http/harvest/stream.py`, never passed it, and only a test
ever did. Both were deleted on 2026-09-24 rather than kept as a reporting path
nothing reported through. The observation they were written for is real: a child
frame applied at the root is how a nested `put` wiped an auction, and if refusals
start mattering again the counter is the shape to bring back, wired to a caller.

One rule carries all the risk: **a ``null`` in a patch deletes the key.** That is
how a close is signalled. A reducer that stores the null instead leaves a price
on the board after the room has moved on, and every ladder built from those
states shows a sale that never happened.
"""

from __future__ import annotations

from typing import Any

from fantabot.domain.harvest.sse import Frame

#: Frames that say nothing about the node. ``keep-alive`` carries ``data: null``,
#: which is indistinguishable from a total deletion if it is not named here.
INERT = frozenset({"keep-alive", "auth_revoked", "cancel"})

State = dict[str, Any]

#: The only path this node ever serves. Anything else is unmodelled.
ROOT = ("/", None)


def apply_frame(state: State, frame: Frame) -> State:
    """Return a new state with ``frame`` applied. Never mutates ``state``."""
    if frame.event in INERT:
        return dict(state)

    if frame.path not in ROOT:
        # Unmodelled, so not guessed at. Applying a child frame at the root is
        # how a nested `put` came to wipe an auction.
        return dict(state)

    if frame.event == "put":
        return dict(frame.data) if isinstance(frame.data, dict) else {}

    if frame.event == "patch" and isinstance(frame.data, dict):
        merged = dict(state)
        for key, value in frame.data.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return merged

    # An event we do not model leaves the node as it was. Refusing it would end
    # a watch over a frame that may simply be new.
    return dict(state)

