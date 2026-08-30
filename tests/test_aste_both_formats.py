"""Both formats are collected. Classic is not a special case.

The poller filtered to Mantra at collection time and threw away 85% of the
population — and `CLAUDE.md` records that we play both: `3584692` is Classic,
`4103937` is Mantra. So the format is a column to select on afterwards, never a
decision taken while collecting.

These are structural assertions rather than a one-off count, because "we
remembered not to filter today" is not a property.
"""

from __future__ import annotations

import ast

import pytest
from _paths import pkg

from fantabot.aste.client import LiveAuctionsClient
from fantabot.aste.registry import from_card

PACKAGE = pkg("aste")

#: Modules that run while collecting. A format comparison in any of them is a
#: filter, whatever it is called.
COLLECTING = ("client.py", "registry.py", "stream.py", "supervisor.py", "landing.py")


def _card(auction_id: str, asta_type: str) -> dict[str, object]:
    return {"fantaleague_id": auction_id, "db": 4, "asta_type": asta_type, "num_teams": 8}


@pytest.mark.parametrize("module", COLLECTING)
def test_no_collecting_module_compares_a_format(module: str) -> None:
    """A literal "mantra" or "classic" in the collection path is a filter waiting
    to happen. The strings belong in the schema's allowed set and in queries."""
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in ("mantra", "classic")
    ]
    assert literals == [], f"{module} names a format: {literals}"


def test_the_client_asks_for_every_format() -> None:
    """`asta_type` is an optional query parameter. Passing it is how coverage was
    lost; omitting it is the whole point of this phase."""
    seen: list[str] = []

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> object:
            return [_card("a", "mantra"), _card("b", "classic")]

    def get(url: str, _headers: dict[str, str]) -> _Response:
        seen.append(url)
        return _Response()

    configs = LiveAuctionsClient(token="t", get=get).live_auctions()
    assert "asta_type" not in seen[0]
    assert {c.asta_type for c in configs} == {"classic", "mantra"}


def test_a_classic_card_survives_the_boundary_unchanged() -> None:
    """Classic is not converted, defaulted or coerced on the way in."""
    assert from_card(_card("a", "classic")).asta_type == "classic"


def test_the_format_survives_the_seed_round_trip() -> None:
    """The filter was gone from collection and the format was still lost — at
    persistence. `harvest scan` fetched both, wrote them to an 11-field positional
    seed with no `asta_type`, and `harvest load --asta-type mantra` then labelled
    185 Classic auctions as Mantra. The coverage goal defeated at the last step.
    """
    from fantabot.aste.registry import from_seed_row, to_seed_rows

    classic = from_card(_card("a", "classic"))
    mantra = from_card(_card("b", "mantra"))
    rows = to_seed_rows([classic, mantra])

    # `asta_type` here is the fallback for legacy rows, deliberately the *wrong*
    # value: a row that carries its own format must ignore it.
    restored = [from_seed_row(row, asta_type="mantra") for row in rows]
    assert [c.asta_type for c in restored] == ["classic", "mantra"]


def test_a_legacy_row_still_reads_with_the_fallback() -> None:
    """The 2026-08-26 seed has eleven fields and no format. It predates storing
    one, and must keep loading."""
    from fantabot.aste.registry import from_seed_row

    legacy = ["a-1", "15", 10, 500, 25, 25, "random", "free", 7, 7, "Lega"]
    assert from_seed_row(legacy, asta_type="mantra").asta_type == "mantra"
