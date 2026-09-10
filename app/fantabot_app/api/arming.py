"""Two locks, named separately — the server-side half of the arming contract.

`CLAUDE.md`: *"Arming needs two locks and a record. `FANTABOT_AUTO_ACT` **and** `--arm`,
both opt-in, because the env var is process-wide `.env` state and the operator who edits it
in the morning is not the one at the keyboard at 21:47."*

The CLI has held this since the first live asta. This is the same contract for a browser,
and a browser makes one of the two properties harder rather than easier: a page can be
reloaded, restored by the session manager, or left open overnight, and none of those may
carry an arming decision forward.

**`arm` is a per-request body field with no default.** Not "defaults to false" — *absent*.
A request that does not say is a 422, not a dry run. That is deliberate and it is the whole
property: the operator who armed it is the one watching, so the intent must be restated on
every request that could act. A default — either way — is a decision the last request makes
for the next one.

**Both closed locks are named, not just the first.** The CLI's own line reports one:

    why = "--arm not given" if settings.fantabot_auto_act else "FANTABOT_AUTO_ACT is false"

That is a ternary over two causes, so an operator with *both* shut fixes one, retries, and
is told about the other. Ten minutes go into editing the wrong file. `Arming.closed` lists
every shut lock.

**`FANTABOT_AUTO_ACT` is read per request**, never captured at import: it comes from `.env`,
and a process that read it once would keep answering with the state of the world when it
started.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The two locks, as exact strings. An operator greps for these.
#:
#: `AUTO_ACT_OFF` is worded identically to the CLI's, because it is the same fact about the
#: same file. `ARM_NOT_REQUESTED` is not `"--arm not given"` — there is no flag in an HTTP
#: request, and a message naming one would send the reader to a terminal they are not using.
AUTO_ACT_OFF = "FANTABOT_AUTO_ACT is false"
ARM_NOT_REQUESTED = "the request did not ask to arm"

#: Job kinds that can act. **An acting job must be stoppable**, which in this app means a
#: `ProcessJob`: `JobRegistry.stop` returns `False` for a thread job — a daemon thread
#: cannot be interrupted from outside — and the route turns that into a 409. A disarm
#: control that answers 409 is a lie, and it is a lie told at the one moment it matters.
#:
#: Empty until 3.3 adds the first one; the guard exists first on purpose, so the rule is in
#: place before there is anything to break it.
ACTING_KINDS: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Arming:
    """Whether this request may act, and — when it may not — every reason it may not.

    Frozen, and a value rather than a flag on a session, because there is nowhere for it to
    be stored: it is computed from one request's body and one read of the environment, and
    it stops existing when the response does.
    """

    armed: bool
    #: Every lock that is shut, in the order an operator would fix them: the ambient one
    #: first (it outlives the request), then the per-request one. Empty when armed.
    closed: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        """One line for a screen, naming **all** the shut locks rather than the first."""
        if self.armed:
            return ""
        return " and ".join(self.closed)


def decide_arming(*, arm: bool, auto_act: bool | None = None) -> Arming:
    """The contract, as a function. Pure when `auto_act` is given.

    `auto_act` is injectable so the property can be tested without touching a real `.env` —
    and `None` means "read it now", which is what a request does.
    """
    if auto_act is None:
        from fantabot.config import settings

        auto_act = bool(settings.fantabot_auto_act)

    closed = tuple(
        name
        for shut, name in ((not auto_act, AUTO_ACT_OFF), (not arm, ARM_NOT_REQUESTED))
        if shut
    )
    return Arming(armed=not closed, closed=closed)


__all__ = ["ACTING_KINDS", "ARM_NOT_REQUESTED", "AUTO_ACT_OFF", "Arming", "decide_arming"]
