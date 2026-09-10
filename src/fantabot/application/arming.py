"""Two locks, named separately — the arming contract, shared by both surfaces.

`CLAUDE.md`: *"Arming needs two locks and a record. `FANTABOT_AUTO_ACT` **and** `--arm`,
both opt-in, because the env var is process-wide `.env` state and the operator who edits it
in the morning is not the one at the keyboard at 21:47."*

The CLI has held this since the first live asta, in prose and a ternary. This is that
contract as a value, in `application/` because *"`interface/` holds no decision the app also
needs"* — and arming is the decision that rule exists for. It landed in `app/` first, which
was the wrong layer: it would have given the browser a copy of a rule the CLI already had,
which is the duplication the whole phase is about.

A browser makes one of the two properties harder rather than easier: a page can be reloaded,
restored by the session manager, or left open overnight, and none of those may carry an
arming decision forward.

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

That was a promise this module made and did not keep. It read
``settings.fantabot_auto_act``, and `settings` is a module singleton built at first import,
so the long-lived app server behind ``POST /lineup/submit`` answered every request with its
boot state: editing `.env` to disarm changed nothing, and neither did changing
``os.environ``. The CLI hid it, being one process per invocation. It now goes through
`config.live_auto_act`, which re-reads on every call and fails closed — and an exported
variable still outranks the file, as it does everywhere else in this repository.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The two locks, by **name**. Not by sentence: the ambient lock is the same fact on both
#: surfaces, but the per-invocation one is a `--arm` flag in a terminal and a body field in
#: a request, and a CLI that told an operator "the request did not ask to arm" — or a page
#: that told them to pass `--arm` — would be sending them somewhere they are not.
#:
#: Each surface renders these through its own `SENTENCES` map; the *fact* is shared, the
#: wording is local.
AUTO_ACT = "FANTABOT_AUTO_ACT"
ARM = "arm"

#: What the CLI says. `interface/lineup.py` and `interface/asta.py` have printed these exact
#: words since the first live asta, and an operator greps for them.
CLI_SENTENCES: dict[str, str] = {
    AUTO_ACT: "FANTABOT_AUTO_ACT is false",
    ARM: "--arm not given",
}


@dataclass(frozen=True, slots=True)
class Arming:
    """Whether this request may act, and — when it may not — every reason it may not.

    Frozen, and a value rather than a flag on a session, because there is nowhere for it to
    be stored: it is computed from one request's body and one read of the environment, and
    it stops existing when the response does.
    """

    armed: bool
    #: Every lock that is shut, **by name**, in the order an operator would fix them: the
    #: ambient one first (it outlives the invocation and nothing on screen shows it), then
    #: the per-invocation one. Empty when armed.
    closed: tuple[str, ...] = ()

    def because(self, sentences: dict[str, str]) -> str:
        """One line, naming **all** the shut locks rather than the first.

        `sentences` is the caller's vocabulary — `CLI_SENTENCES` for a terminal, the app's
        own for a request. A shared sentence would have to name one surface's control.
        """
        if self.armed:
            return ""
        return " and ".join(sentences[lock] for lock in self.closed)


def decide_arming(*, arm: bool, auto_act: bool | None = None) -> Arming:
    """The contract, as a function. Pure when `auto_act` is given.

    `auto_act` is injectable so the property can be tested without touching a real `.env` —
    and `None` means "read it now", which is what a request does.
    """
    if auto_act is None:
        from fantabot.config import live_auto_act

        auto_act = live_auto_act()

    closed = tuple(
        name for shut, name in ((not auto_act, AUTO_ACT), (not arm, ARM)) if shut
    )
    return Arming(armed=not closed, closed=closed)


__all__ = ["ARM", "AUTO_ACT", "CLI_SENTENCES", "Arming", "decide_arming"]
