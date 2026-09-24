"""The FantaLab ``update_type`` vocabulary has one home, and these tests are what keeps it.

`domain/asta/live.py` and `domain/harvest/turn.py` read the same ``auction/<fl>`` node --
`live`'s own docstring says its close-state parser is for replays of a captured snapshot
stream, and that stream is exactly what `harvest` folds -- and each declared its own
``CLOSE = "close_auction"``. Nothing could see the pair: Python interns both literals, so
``live.CLOSE is turn.CLOSE`` was already `True` with two independent declarations, and an
identity assertion would have passed against the defect. So the rule here is about the
**source**, not the values: `test_no_second_home` walks the AST of every module in the
package and fails on a token spelled anywhere but its home.

The repair deliberately does **not** point `domain/asta` at `domain/harvest`. That edge is
legal under `tests/test_layers.py` and still wrong: it would make the asta advisory depend
on the harvest package for a string. `test_the_two_features_stay_independent` pins that the
cure was not worse than the disease.
"""

from __future__ import annotations

import ast

from _importgraph import PACKAGE, direct_imports, reaches

from fantabot.domain.asta import live
from fantabot.domain.harvest import turn
from fantabot.domain.shared import update_types

HOME = "fantabot.domain.shared.update_types"

#: name -> the string the platform actually puts on the wire. Pinned literally: these are
#: not ours to rename, and a typo here is a fold that silently classifies nothing.
TOKENS: dict[str, str] = {
    "FIRST_CALL": "first_call",
    "RAISE": "raise",
    "CLOSE": "close_auction",
}

#: The one site outside the home that still spells a token, listed rather than excused.
#: `domain/asta/bid.py` builds an RTDB *write* body -- there the string is a field of a
#: payload going out, not a state being classified on the way in -- and that module was
#: outside the file set of the change that made this home. Named here so a fourth
#: declaration cannot be added quietly: a new entry has to be argued for in review.
KNOWN_WRITE_SITES: set[tuple[str, str]] = {("domain/asta/bid.py", "raise")}


def test_tokens_are_the_platform_strings() -> None:
    """The home spells each token exactly as FantaLab writes it."""
    assert {name: getattr(update_types, name) for name in TOKENS} == TOKENS


def test_no_second_home() -> None:
    """No module but the home spells a token -- which is the defect this file is about.

    An AST walk rather than a text scan, so a token named inside a docstring or a comment
    (both packages explain ``close_auction`` at length, and should) is not a hit: only a
    string constant *equal to* a token is one.
    """
    values = set(TOKENS.values())
    found: set[tuple[str, str]] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        if relative == "domain/shared/update_types.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in values:
                found.add((relative, node.value))

    assert found == KNOWN_WRITE_SITES, (
        "a platform update_type is declared outside domain/shared/update_types.py: "
        f"{sorted(found - KNOWN_WRITE_SITES)}"
    )


def test_both_readers_take_their_token_from_the_home() -> None:
    """Neither reader re-derives it -- the constant is imported, not recomputed."""
    assert HOME in direct_imports("fantabot.domain.asta.live")
    assert HOME in direct_imports("fantabot.domain.harvest.turn")
    assert live.CLOSE == update_types.CLOSE == "close_auction"


def test_the_two_features_stay_independent() -> None:
    """The cure is not the cross-feature edge: neither package reaches the other.

    Transitive, because a re-export shim is what a direct-import check cannot see -- see
    `tests/_importgraph.py`.
    """
    assert not reaches("fantabot.domain.asta.live", "fantabot.domain.harvest")
    assert not reaches("fantabot.domain.harvest.turn", "fantabot.domain.asta")


def test_bidding_is_built_from_the_home_and_leaves_no_literal() -> None:
    """`BIDDING` stays with the fold that applies it, but holds no token of its own.

    It is not a token -- it is which of them this fold reads as a price -- so moving it
    would have put a rule where the vocabulary lives. Splitting the *strings* is what was
    forbidden, and `test_no_second_home` is what proves they are not split.
    """
    assert isinstance(turn.BIDDING, frozenset)
    assert set(turn.BIDDING) == {
        update_types.FIRST_CALL,
        update_types.RAISE,
        update_types.CLOSE,
    }
