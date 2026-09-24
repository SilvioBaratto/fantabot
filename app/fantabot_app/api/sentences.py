"""What this app says when an arming lock is shut. One table, for both acting surfaces.

`application/arming` names the two locks and deliberately does **not** name the sentences:
the ambient lock is the same fact everywhere, but the per-invocation one is a `--arm` flag
in a terminal and a body field in a request, so the *fact* is shared and the wording is
local. That module renders through a caller-supplied map — `CLI_SENTENCES` for a terminal,
this one for a request — and a page that told an operator to pass `--arm` would be sending
them to a terminal they are not using.

`POST /lineup/submit` and `POST /asta/room/bid` are two routes on one surface, so "local to
the surface" is one table and not two. It was two, identical in every sentence and keyed
differently: `endpoints/lineup.py` used the imported `ARM`/`AUTO_ACT` constants and
`endpoints/room_bid.py` the string literals `"arm"` and `"FANTABOT_AUTO_ACT"`.

**That asymmetry was a live fault, not an inconsistency.** `Arming.because` looks each shut
lock up by the name `application/arming` gave it, so renaming a lock there leaves the
constant-keyed copy correct and makes the literal-keyed one raise `KeyError` — from inside
the branch whose entire job is to explain a refusal, on the route that spends credits.
Keyed by the constants, a rename is a rename in one place and mypy sees the rest.
"""

from __future__ import annotations

from fantabot.application.arming import ARM, AUTO_ACT

#: Keyed by `application/arming`'s constants, never by their current spelling.
APP_SENTENCES: dict[str, str] = {
    AUTO_ACT: "FANTABOT_AUTO_ACT is false",
    ARM: "the request did not ask to arm",
}

__all__ = ["APP_SENTENCES"]
