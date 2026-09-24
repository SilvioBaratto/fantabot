"""What submitting a lineup can raise that is neither a token nor a transport problem.

The platform reads `starts[i]` against **its own** slot i (`S.schemes.mantra`, pinned in
`mantra_starts_order.json`) and refuses a `no` or `-1*` cell with a `LUP0xx` code —
`LUP009` "the formation module is not allowed", observed live 2026-09-02. Those refusals
were ours: `starts[]` went out in the PDF table's row order, not the platform's. A `-1`
cell is not refused at all — it is accepted and scored as a malus, so a refusal is never
the check for one (`positional.py` is). That is a lineup problem, not a credential
problem, so it is its own family rather than a `TokenError`: the fix is to rebuild
`starts[]`, never to re-authenticate.

No message carries a token, and the response body is never echoed verbatim — a refusal
carries the body's `code` and `message` fields and nothing else, in the style of
`domain/tokens/errors`.
"""

from __future__ import annotations


class LineupError(Exception):
    """Base for lineup failures, so callers can catch the family."""


class UnknownMarleRole(LineupError):
    """A numeric `marle` role code with no entry in the mapping.

    Fail-closed by number so the code can be added (notably `B`, absent when the map was
    derived) rather than a role being guessed.
    """

    def __init__(self, code: int) -> None:
        super().__init__(
            f"unknown marle role code {code} — extend MARLE_TO_ROLE. Nothing was mapped."
        )
        self.code = code


class NoCompetition(LineupError):
    """No active competition includes our team — cannot resolve where to field a lineup."""

    def __init__(self, tid: int) -> None:
        super().__init__(
            f"no active competition includes team {tid}. Check the league, or pass "
            "`--competition`."
        )
        self.tid = tid


class CompetitionAmbiguous(LineupError):
    """Several active competitions include our team — the operator must pick one."""

    def __init__(self, competitions: tuple[int, ...]) -> None:
        super().__init__(
            f"several competitions include our team {list(competitions)} — pass "
            "`--competition <id>` to choose."
        )
        self.competitions = competitions


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
    """A roster id with no role — the roster cannot be assembled.

    Fail-closed by name: guessing a role would build a lineup the platform rejects, so the
    id is surfaced rather than dropped.

    **The role comes from the lega, not from a scrape.** `lineUpInfo` carries it — the
    marle codes in `role` on Mantra, `fcrle` on Classic — so an empty one means the
    platform sent this roster row without a role, or sent a code `marle`/`fcrle` does not
    map. No local scrape can supply it, which is why the message points at the lega read
    and not at `db scrape quotazioni`, as it did until 2026-09-24.
    """

    def __init__(self, player_id: int) -> None:
        super().__init__(
            f"roster player {player_id} has no role in the lega's lineUpInfo — cannot "
            "place him. Re-read the lineup (`fantabot lineup show`) and check the id; "
            "nothing was assembled."
        )
        self.player_id = player_id


class LineupRejected(LineupError):
    """The platform refused the formation (`LUP0xx`).

    `code` is kept for the caller; the common case is `LUP009`, returned when `starts[]`
    does not positionally satisfy the module's slots. `message` is the platform's own
    sentence, `""` when it sent none — the code says *that* it refused, only the message
    can say what it read. Rebuild from `schema.slots` and recheck with `positional`, not
    `asta.legality`, which is set-based and cannot see order.
    """

    def __init__(self, code: str, message: str = "") -> None:
        said = f": {message}" if message else ""
        super().__init__(
            f"apileague refused the formation ({code}{said}). The lineup is not fieldable "
            "as sent — rebuild starts[] positionally from the schema and recheck it with "
            "the positional guard before resubmitting."
        )
        self.code = code
        self.message = message


class OpponentUnavailable(LineupError):
    """The lega's calculated scores cannot carry an opponent distribution.

    Either too few rounds have been calculated to smooth at all, or every one of them read
    the same total, which is a point mass rather than a distribution. The projection path
    refuses and the run falls back to `indexCompare`, which needs no opponent: a KDE over
    four scores would be a guess with a probability attached.
    """

    def __init__(self, scored: int, minimum: int, *, reason: str = "") -> None:
        said = reason or f"only {scored} calculated round(s), and {minimum} are the floor"
        super().__init__(f"no opponent distribution: {said}. Nothing was fitted.")
        self.scored = scored
        self.minimum = minimum
