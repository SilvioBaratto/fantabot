"""`positional` — every starter judged at the platform's own slot i, as the platform judges him.

The builder matches on role **sets**, so it cannot see order; a set-based check passes an XI
the platform scores with a malus or refuses outright. This reads one `mantra_compat.json`
cell per position, in the pinned order, and passes only an XI whose eleven cells are `ok`.
"""

from __future__ import annotations

import pytest

from fantabot.domain.lineup import positional
from fantabot.domain.lineup.positional import Cell


def _roles(*labels: str) -> list[frozenset[str]]:
    return [frozenset(label.split("/")) for label in labels]


def test_a_442_laid_out_in_the_pdf_row_order_is_refused_at_the_m_only_player() -> None:
    """The silent-malus case: `mantra_schemi.json` has M/C at position 6, the platform has C.

    Built in the old order, an M-only player lands in the platform's pure-C slot. That is a
    `-1` cell, so the platform would have **accepted** the lineup and scored him a malus.
    """
    xi = _roles("POR", "DS", "DC", "DC", "DD", "E", "M", "C", "W", "A", "PC")

    assert positional.violations("442", xi) == (Cell(6, frozenset({"C"}), "M", "-1"),)


def test_the_same_players_in_the_platforms_order_pass() -> None:
    xi = _roles("POR", "DS", "DC", "DC", "DD", "E", "C", "M", "W", "A", "PC")

    assert positional.violations("442", xi) == ()


def test_the_ui_saved_3412_has_exactly_one_malus_and_nothing_refused() -> None:
    """Ground truth independent of the bundle: the operator's own UI save, 2026-09-02.

    The body is `docs/leghe-api.md`'s captured POST (the UI lays `starts[]` out by position).
    Roles from `league_player_pool`, 2026-09-21: Mandas, Bremer, Leysen, Zè Pedro, Wesley,
    Kessiè, Coulibaly, Holm, Tavares, Paz, Gonzalez. The platform saved it — so it holds no
    refused cell — and Tavares (Ds/E) sits in the T slot, one `-1`.
    """
    xi = _roles("POR", "DC", "DS/DC", "DD/DC", "E", "C", "M/C", "DD/E", "DS/E", "T/A", "W/A")

    cells = positional.cells("3412", xi)

    assert [c for c in cells if c.value in {"no", "-1*"}] == []
    assert [(c.position, c.value) for c in cells if c.value != "ok"] == [(8, "-1")]


def test_a_multi_role_player_is_judged_by_his_best_role() -> None:
    """The platform's `getMinRoleMalus` takes the minimum over a player's roles."""
    xi = _roles("POR", "DC", "DC", "DC", "E", "M/C", "C", "E", "T", "A", "PC")

    assert positional.cells("3412", xi)[5] == Cell(5, frozenset({"C"}), "C", "ok")


def test_cells_come_from_the_schemas_own_rows() -> None:
    """4-1-4-1 is the one schema where a W in a T slot is `no` — never, not even after a
    forced substitution. Everywhere else it is `-1*`. Both refuse at submission; only a
    lookup in the schema's own rows can tell them apart."""
    in_4141 = _roles("POR", "DS", "DC", "DC", "DD", "M", "W", "W", "C", "E", "PC")
    in_4231 = _roles("POR", "DS", "DC", "DC", "DD", "M", "M", "W", "W", "W", "PC")

    assert positional.cells("4141", in_4141)[7] == Cell(7, frozenset({"T"}), "W", "no")
    assert positional.cells("4231", in_4231)[8] == Cell(8, frozenset({"T"}), "W", "-1*")


def test_the_refusal_names_the_position_the_slot_the_role_and_the_cell() -> None:
    xi = _roles("POR", "DS", "DC", "DC", "DD", "E", "M", "C", "W", "A", "PC")

    assert positional.refusal("442", xi) == "[6] C: M -1"


def test_an_xi_that_passes_has_no_refusal() -> None:
    xi = _roles("POR", "DS", "DC", "DC", "DD", "E", "C", "M", "W", "A", "PC")

    assert positional.refusal("442", xi) == ""


def test_an_xi_of_the_wrong_length_raises() -> None:
    with pytest.raises(ValueError, match="11"):
        positional.cells("442", _roles("POR", "DC"))
