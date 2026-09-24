"""The four panes, asserted against a recording Console rather than through the CLI.

`Live(screen=True)` writes to the terminal's alternate buffer, where captured output is empty
— a `CliRunner` test over the live command can only ever check the exit code, which proves the
command starts and nothing about what it draws. So `render` returns a renderable instead of
printing one, and this drives it directly.

The other property worth pinning is that `render` is **total**: given any frame it paints
without raising and without I/O. A pane that can throw takes the screen with it at 21:47, on
the frame that mattered.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from fantabot.application.asta_room import RoomFrame
from fantabot.domain.asta.report import ListoneRow
from fantabot.interface.room_view import error_overlay, render


def _frame(**kw: object) -> RoomFrame:
    base: dict[str, object] = dict(
        lot_id="uuid-a1", lot_name="Bomber", price=34, high_bidder="Rivali",
        seconds_left=6.2, node="auction", target="uuid-a1", walk_away=77,
        provenance="floor", decision="bid", reason=None, note=None,
        credits_left=309, max_cap=285, owned=("100",), plan=("100", "200"),
        unresolved_sales=0, walkaways={}, schemi_open=7, recent=(),
    )
    return RoomFrame(**{**base, **kw})  # type: ignore[arg-type]


def _row(name: str = "Bomber", status: str = "open", walk: int | None = 77) -> ListoneRow:
    return ListoneRow(
        player_id="200", name=name, team="MIL", roles=("A",),
        value=9.4, price=40, walk_away=walk, status=status,
    )


def _pane(painted: str, title: str) -> str:
    """One pane out of the stack. `render` returns a `Group`, so each pane is a run of lines.

    From the bordered line carrying the title down to the line before the next one — which
    is what makes "the COPILOTA box shows no credit figure" a checkable claim rather than a
    statement about the whole screen, where `credits 309` is drawn on purpose.
    """
    lines = painted.splitlines()
    start = next(i for i, line in enumerate(lines) if f" {title} " in line)
    rest = [i for i, line in enumerate(lines[start + 1 :], start + 1) if line.startswith("╭")]
    return "\n".join(lines[start : rest[0] if rest else len(lines)])


def _markers(painted: str) -> dict[str, str]:
    """`{player name: the marker cell}` for every row drawn in the LISTONE pane.

    The marker is the table's last column and the panel draws no edge, so the cell is what
    sits between the final two `│` on the row's line. Read positionally rather than by
    substring: `assert "ours" in painted` is satisfied by a *name* that happens to be
    `ours`, and an emptied marker map still leaves every name on screen.
    """
    out: dict[str, str] = {}
    for line in _pane(painted, "LISTONE").splitlines():
        cells = [cell.strip() for cell in line.split("│")]
        if len(cells) >= 3 and cells[1] in {"ours", "taken", "open"}:
            out[cells[1]] = cells[-2]
    return out


def _paint(frame: RoomFrame, rows: list[ListoneRow] | None = None, **kw: object) -> str:
    console = Console(record=True, force_terminal=True, width=200, height=60, no_color=True)
    console.print(render(frame, rows if rows is not None else [_row()], **kw))  # type: ignore[arg-type]
    return console.export_text()


class TestTheFourPanes:
    def test_all_four_titles_appear(self) -> None:
        painted = _paint(_frame())

        for title in ("LOT", "MODEL", "LISTONE"):
            assert title in painted
        assert "credits 309" in painted, "the header pane"

    def test_the_lot_shows_the_player_the_price_and_the_next_rung(self) -> None:
        """The rung is the number the operator is about to pay, so it is read as a phrase.

        This asserted `"34" in painted and "35" in painted` until 2026-09-24 — two bare
        digit pairs over a 200x60 screen carrying `credits 309`, `MAX 285`, `77`, `6.2`,
        `9.4` and `40`. Each happens to occur once today, so the test was not yet vacuous;
        it goes vacuous the first time any other number on the screen contains them, and
        it never said the two were a price and its successor rather than two unrelated
        numbers in different panes.
        """
        painted = _paint(_frame())

        assert "Bomber" in painted
        assert "price 34 → next 35" in painted

    def test_the_walkaway_shows_its_provenance_and_is_never_fused(self) -> None:
        """A number nobody can argue with is a number nobody can correct."""
        painted = _paint(_frame())

        assert "walk-away 77" in painted
        assert "floor" in painted

    def test_the_node_is_visible_so_an_assegna_lot_is_not_a_mystery(self) -> None:
        assert "assign" in _paint(_frame(node="assign"))

    def test_a_refusal_names_the_guard(self) -> None:
        painted = _paint(_frame(decision="pass", reason="max_cap"))

        assert "PASS" in painted
        assert "max_cap" in painted


class TestTheArmedBadge:
    def test_armed_says_so_in_words_not_only_in_colour(self) -> None:
        """Colour alone is not a signal — `NO_COLOR` is set in this repo's own test env."""
        assert "ARMED" in _paint(_frame(), armed=True)
        assert "REAL CREDITS" in _paint(_frame(), armed=True)

    def test_disarmed_says_dry_run(self) -> None:
        assert "DRY RUN" in _paint(_frame(), armed=False)


class TestRenderIsTotal:
    @pytest.mark.parametrize(
        "frame",
        [
            RoomFrame(
                lot_id=None, lot_name=None, price=0, high_bidder=None, seconds_left=None,
                node="auction", target=None, walk_away=None, provenance=None,
                decision="waiting", reason=None, note=None, credits_left=500, max_cap=471,
                owned=(), plan=(), unresolved_sales=0, walkaways={},
                schemi_open=0, recent=(),
            ),
            RoomFrame(
                lot_id="u", lot_name=None, price=0, high_bidder=None, seconds_left=0.0,
                node="assign", target=None, walk_away=None, provenance=None,
                decision="hold", reason=None, note="lot is not in the listone",
                credits_left=0, max_cap=0, owned=(), plan=(), unresolved_sales=3,
                walkaways={}, schemi_open=0, recent=(),
            ),
        ],
        ids=["nothing-on-the-block", "everything-unknown"],
    )
    def test_a_sparse_frame_still_paints(self, frame: RoomFrame) -> None:
        assert _paint(frame, rows=[])

    def test_an_empty_listone_paints_the_pane_anyway(self) -> None:
        assert "LISTONE" in _paint(_frame(), rows=[])

    def test_every_row_status_has_a_marker(self) -> None:
        """`ours` / `sold` / nothing — the column an operator scans to find their own rosa.

        The assertion was `assert _paint(...)`, a truthiness check on a rendered string,
        under a name promising a marker check (2026-09-24). It did catch a status with no
        entry in the marker map — that is a `KeyError` out of `_listone_pane` — and
        nothing else: the three markers could all render empty and it stayed green.
        """
        rows = [_row(name=status, status=status) for status in ("ours", "taken", "open")]

        marked = _markers(_paint(_frame(), rows=rows))

        # `open` is deliberately blank: a marker on every row is a marker on none.
        assert marked == {"ours": "ours", "taken": "sold", "open": ""}


class TestTheCopilotPane:
    def test_an_outage_says_so_and_moves_nothing_else(self) -> None:
        """The engine's number renders at t=0 whatever the network is doing."""
        painted = _paint(_frame(), copilot_offline=True)

        assert "copilota: offline" in painted
        assert "walk-away 77" in painted, "the number is untouched by the copilot's weather"
        assert "LISTONE" in painted

    def test_nothing_yet_is_not_the_same_as_offline(self) -> None:
        """Two seconds after a lot opens there is no commentary and nothing is wrong."""
        painted = _paint(_frame(), advice=None, copilot_offline=False)

        assert "COPILOTA" in painted
        assert "offline" not in painted

    def test_commentary_renders_with_its_risks_and_confidence(self) -> None:
        from fantabot.domain.asta.copilot import Commentary

        said = Commentary(
            headline="ballottaggio aperto", why="due punte per un posto",
            risks=["parte in panchina"], watch=["Riserva"],
            confidence="medium", disagrees_with_plan=True,
        )
        painted = _paint(_frame(), advice=said)

        assert "ballottaggio aperto" in painted
        assert "parte in panchina" in painted
        assert "medium" in painted

    def test_the_pane_never_shows_a_credit_figure(self) -> None:
        """Two locks. The schema cannot carry one, and the pane does not add one.

        Only the first was asserted until 2026-09-24: the second read `assert _paint(...)`,
        the truthiness of a rendered string, which is green for any pane that draws at all
        — including one that had grown a `credits {frame.credits_left}` line. The frame
        below carries three distinctive figures and the COPILOTA box is sliced out and
        checked for every one of them.
        """
        from fantabot.domain.asta.copilot import Commentary

        said = Commentary(
            headline="ok", why="niente di nuovo", confidence="low", disagrees_with_plan=False
        )

        assert set(Commentary.model_fields) == {
            "headline", "why", "risks", "watch", "confidence", "disagrees_with_plan"
        }

        painted = _paint(_frame(credits_left=309, max_cap=285, walk_away=77), advice=said)
        pane = _pane(painted, "COPILOTA")

        assert "ok" in pane and "niente di nuovo" in pane, "the pane did not draw at all"
        for figure in ("309", "285", "77"):
            assert figure not in pane, f"the copilot pane rendered {figure}"


class TestTheStaleBanner:
    """A failed poll leaves the previous frame on screen. Nothing else says so.

    A frozen screen and a quiet room are pixel-identical, and they are the two states the
    operator most needs to tell apart: in one the bot is still bidding and in the other it is
    not. The banner is the whole difference.
    """

    def _overlay(self, view: object | None, message: str, consecutive: int) -> str:
        console = Console(record=True, force_terminal=True, width=200, height=60, no_color=True)
        console.print(error_overlay(view, message, consecutive=consecutive))  # type: ignore[arg-type]
        return console.export_text()

    def test_it_names_the_failure_and_the_streak(self) -> None:
        painted = self._overlay(None, "ReadTimeout: blip", 3)

        assert "STALE" in painted
        assert "ReadTimeout: blip" in painted
        assert "3" in painted, "the streak length distinguishes a blip from a dead link"

    def test_the_last_good_screen_survives_underneath(self) -> None:
        """Blanking it would take the walk-away column away at exactly the moment the operator
        has to bid by hand instead."""
        view = render(_frame(), [_row(name="Bomber", walk=77)])
        painted = self._overlay(view, "ConnectTimeout: gone", 1)

        assert "STALE" in painted
        assert "Bomber" in painted
        assert "77" in painted, "the number the operator now has to type themselves"

    def test_it_paints_with_no_previous_screen(self) -> None:
        """The link can be down before the first frame is ever drawn."""
        painted = self._overlay(None, "ConnectError: down", 1)

        assert "STALE" in painted

    def test_it_never_raises_on_an_awkward_message(self) -> None:
        """`error_overlay` joins `render` in being total: it runs on the failure path, and a
        painter that can throw there turns a recoverable outage into a dead command."""
        for message in ("", "x" * 500, "[not-a-rich-tag] {0} %s", "\n\n"):
            assert "STALE" in self._overlay(None, message, 1)
