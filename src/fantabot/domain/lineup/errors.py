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
    """Base for lineup-submission failures, so callers can catch the family."""


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
