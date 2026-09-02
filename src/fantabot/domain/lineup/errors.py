"""What submitting a lineup can raise that is neither a token nor a transport problem.

The platform validates the formation **positionally** against the chosen module's schema
and refuses one it cannot field with a `LUP0xx` code — `LUP009` "the formation module is
not allowed" is the one observed live 2026-09-02, returned even for a role-for-role swap
whose ordering broke the schema. That is a lineup problem, not a credential problem, so it
is its own family rather than a `TokenError`: the fix is to rebuild `starts[]`, never to
re-authenticate.

No message carries a token, and the response body is never echoed verbatim — the code
alone names the failure, in the style of `domain/tokens/errors`.
"""

from __future__ import annotations


class LineupError(Exception):
    """Base for lineup failures, so callers can catch the family."""


class NoFieldableModule(LineupError):
    """The roster cannot field any of the allowed modules with natural roles.

    Not a transport failure — the rosa itself is short of a role the schemi need (a
    goalkeeper, most often). Names the modules tried so the gap is visible.
    """

    def __init__(self, modules: tuple[str, ...]) -> None:
        super().__init__(
            f"the roster fields none of the allowed modules {list(modules)} with natural "
            "roles — it is missing a role the schemi require (check the goalkeeper and the "
            "back line). Nothing was built."
        )
        self.modules = modules


class BenchIncomplete(LineupError):
    """The bench cannot be filled — no reserve keeper, or fewer reserves than the bench size.

    The platform requires a goalkeeper in the first bench slot and a full bench, so an
    incomplete one is refused rather than submitted and rejected. The message says which.
    """


class RosterIncomplete(LineupError):
    """A roster id with no Mantra role — the roster cannot be assembled.

    Fail-closed by name: guessing a role would build a lineup the platform rejects, so the
    id is surfaced (scrape `quotazioni`, or check the id) rather than dropped.
    """

    def __init__(self, player_id: int) -> None:
        super().__init__(
            f"roster player {player_id} has no Mantra role in quotazioni — cannot place "
            "him. Refresh the scrape (`fantabot db scrape quotazioni`) or check the id; "
            "nothing was assembled."
        )
        self.player_id = player_id


class LineupRejected(LineupError):
    """The platform refused the formation (`LUP0xx`).

    `code` is kept for the caller; the common case is `LUP009`, returned when `starts[]`
    does not positionally satisfy the module's slots. Rebuild from the schema and
    revalidate with `domain/asta/legality` before resubmitting.
    """

    def __init__(self, code: str) -> None:
        super().__init__(
            f"apileague refused the formation ({code}). The lineup is not fieldable as "
            "sent — rebuild starts[] positionally from the schema and revalidate with "
            "legality before resubmitting."
        )
        self.code = code
