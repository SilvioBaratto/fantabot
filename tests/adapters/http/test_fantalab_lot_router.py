"""A lot can arrive on either of two nodes, and a raise has to go back to the one it came on.

`docs/fantalab/06 §10.6`, proved live on 2026-08-28: the admin of a `random` room has two
controls and they route a lot to **different** nodes. CHIAMA random puts it on
`auction/<fl>`; ASSEGNA random puts it on `assign/<fl>`. A bidder subscribed only to
`auction/` bids on nothing for the whole of an ASSEGNA-run evening — no error, no empty
snapshot, just a room that never seems to have a lot on the block.

The reducer is shared, so the fix is one more read feeding the same state machine. The part
that is easy to get wrong is the write: a raise must PATCH the node the lot was **read** from,
because the other one holds a different lot or nothing at all.

⚠ Reading `assign/` is proved. **Writing it as a participant has never been tested**
(`docs/fantalab/06 §10.5`). If it 401s, the router still shows those lots and they are bid by
hand — which is why the node travels on the frame rather than being assumed.
"""

from __future__ import annotations

from typing import Any

from fantabot.adapters.http.fantalab.room import LotRouter

LIVE = {"player_id": "kean", "price": 3, "user_id": "rival", "last_bid_time": 0}
RESET = {"update_type": "reset"}


def _router(auction: Any, assign: Any) -> LotRouter:
    reads: dict[str, Any] = {"auction": auction, "assign": assign}
    return LotRouter(read=lambda node: reads[node])


class TestWhichNodeAnsweredScribe:
    def test_a_called_lot_comes_from_auction(self) -> None:
        snapshot, node = _router(LIVE, None).read_lot()

        assert node == "auction"
        assert snapshot is not None and snapshot["player_id"] == "kean"

    def test_an_assigned_lot_is_found_by_falling_through(self) -> None:
        """The whole point: without this the bot sees an empty room all evening."""
        snapshot, node = _router(None, LIVE).read_lot()

        assert node == "assign"
        assert snapshot is not None and snapshot["player_id"] == "kean"

    def test_auction_wins_when_both_hold_a_lot(self) -> None:
        """Between lots `auction/` carries a `reset` and `assign/` briefly returns to null
        (§10.6), so a genuine overlap means the called lot is the live one."""
        other = {**LIVE, "player_id": "other"}
        snapshot, node = _router(LIVE, other).read_lot()

        assert (node, snapshot["player_id"]) == ("auction", "kean")  # type: ignore[index]

    def test_a_reset_on_auction_is_not_a_lot_and_falls_through(self) -> None:
        """`update_type: reset` is what sits on `auction/` between lots. Treating it as a lot
        would hide every assigned player behind a node that is technically non-empty."""
        snapshot, node = _router(RESET, LIVE).read_lot()

        assert node == "assign"
        assert snapshot is not None and snapshot["player_id"] == "kean"

    def test_no_lot_anywhere_reports_the_default_node(self) -> None:
        snapshot, node = _router(None, None).read_lot()

        assert snapshot is None
        assert node == "auction"


class TestTheWriteGoesBackWhereTheLotCameFrom:
    def test_it_patches_the_node_that_answered(self) -> None:
        sent: list[tuple[dict[str, Any], str]] = []
        router = LotRouter(
            read=lambda node: {"auction": None, "assign": LIVE}[node],
            write=lambda payload, node: sent.append((payload, node)),
        )

        router.read_lot()
        router.write_raise({"price": 4})

        assert sent == [({"price": 4}, "assign")]

    def test_before_any_read_it_writes_to_auction(self) -> None:
        """A raise cannot precede a lot, but a default that silently picked the wrong node
        would be a 401 read as a lost race."""
        sent: list[tuple[dict[str, Any], str]] = []
        LotRouter(read=lambda _n: None, write=lambda p, n: sent.append((p, n))).write_raise(
            {"price": 1}
        )

        assert sent == [({"price": 1}, "auction")]
