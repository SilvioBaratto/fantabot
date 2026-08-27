"""Folding SSE frames into the merged state the rest of the pipeline consumes.

This is the seam between the two collection paths. The poller wrote merged
states directly; the live path arrives at the same shape here, so ``reconstruct``
needs no second implementation and the recorded evening keeps working as a
regression test for both.

One rule carries all the risk: **a ``null`` in a patch deletes the key.** That is
how a close is signalled. A reducer that stores the null instead leaves a price
on the board after the room has moved on, and every ladder built from those
states shows a sale that never happened.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fantabot.aste.sse import Frame

#: Frames that say nothing about the node. ``keep-alive`` carries ``data: null``,
#: which is indistinguishable from a total deletion if it is not named here.
INERT = frozenset({"keep-alive", "auth_revoked", "cancel"})

State = dict[str, Any]


def apply_frame(state: State, frame: Frame) -> State:
    """Return a new state with ``frame`` applied. Never mutates ``state``."""
    if frame.event in INERT:
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
