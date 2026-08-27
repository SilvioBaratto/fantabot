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
now leaves the state alone and is **counted**, because refusing in silence is the
failure this phase keeps finding in itself.

One rule carries all the risk: **a ``null`` in a patch deletes the key.** That is
how a close is signalled. A reducer that stores the null instead leaves a price
on the board after the room has moved on, and every ladder built from those
states shows a sale that never happened.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from fantabot.aste.sse import Frame

#: Frames that say nothing about the node. ``keep-alive`` carries ``data: null``,
#: which is indistinguishable from a total deletion if it is not named here.
INERT = frozenset({"keep-alive", "auth_revoked", "cancel"})

State = dict[str, Any]

#: The only path this node ever serves. Anything else is unmodelled.
ROOT = ("/", None)


def unsupported_paths() -> Counter[str]:
    """A counter for paths the reducer refused, so a caller can surface them."""
    return Counter()


def apply_frame(
    state: State, frame: Frame, seen: Counter[str] | None = None
) -> State:
    """Return a new state with ``frame`` applied. Never mutates ``state``.

    ``seen`` collects paths this refuses, if a caller wants to know.
    """
    if frame.event in INERT:
        return dict(state)

    if frame.path not in ROOT:
        # Unmodelled, so not guessed at. Applying a child frame at the root is
        # how a nested `put` came to wipe an auction.
        if seen is not None:
            seen[str(frame.path)] += 1
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


def fold(frames: Iterable[Frame], initial: State | None = None) -> State:
    """Apply every frame in order."""
    state: State = dict(initial or {})
    for frame in frames:
        state = apply_frame(state, frame)
    return state
