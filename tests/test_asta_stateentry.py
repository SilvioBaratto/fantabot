"""Fast state entry: 'malen a luca 192' -> a resolved sale. Pure parts, synchronous.

The LLM turns the free-text entry into a `StateEntry` (player name, price, team); `resolve`
matches the fuzzy name against the listone. An ambiguous or unmatched name is surfaced as
``None`` — never guessed — because a wrong player id at 21:47 is worse than asking again.

The agent call that produces the `StateEntry` was deleted in P11-5: it had no caller and no
test. These four cover everything the module still has.
"""

from __future__ import annotations

from fantabot.asta_engine.stateentry import ResolvedEntry, StateEntry, build_prompt, resolve

NAMES = {"1": "Malen", "2": "Zaccagni", "3": "Malinovskyi"}


def test_build_prompt_carries_the_raw_text() -> None:
    assert "malen a luca 192" in build_prompt("malen a luca 192")


def test_resolve_matches_a_unique_fuzzy_name() -> None:
    entry = StateEntry(player="malen", price=192, team="luca")
    assert resolve(entry, NAMES) == ResolvedEntry(player_id="1", price=192, team="luca")


def test_resolve_surfaces_an_ambiguous_name_as_none() -> None:
    # "mal" is inside both Malen and Malinovskyi — do not guess.
    assert resolve(StateEntry(player="mal", price=10, team="x"), NAMES) is None


def test_resolve_surfaces_an_unknown_name_as_none() -> None:
    assert resolve(StateEntry(player="Vlahovic", price=50, team="x"), NAMES) is None
