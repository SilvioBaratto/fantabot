"""The four panes, painted from one frozen frame. The only Rich-heavy module in the repo.

`docs/fantalab/00 §15`: *il numero non dipende dalla rete* and *leggibile a colpo d'occhio*.
Both are structural here rather than aspirational — `render` takes a `RoomFrame` and performs
no I/O at all, so there is no socket for a pane to block on and no branch that can raise
mid-paint and leave half a screen.

**`render` returns a renderable; it does not print.** That is what makes the four panes
assertable without a terminal: `Live(screen=True)` writes to the alternate buffer, where
captured output is empty, so a test driving the command through `CliRunner` can only ever
check the exit code. A recording `Console` over this function checks the pixels.

The shared `Console` is a parameter and is never constructed here — `interface/console.py`
owns the one instance, built bare so every setting comes from the environment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from fantabot.application.asta_room import ResolvedRoom, RoomFrame
    from fantabot.domain.asta.copilot import Commentary
    from fantabot.domain.asta.report import ListoneRow

#: Under this many seconds the LOT pane goes red. The room's raise timer is 10 s by default,
#: so two is about one human reaction plus one poll.
URGENT_SECONDS = 2.0


def _header(frame: RoomFrame, room: ResolvedRoom | None, *, armed: bool) -> RenderableType:
    """League, seat, credits, and — unmissably — whether this run spends real money."""
    left = Text()
    if room is not None:
        left.append(f"{room.fantaleague_id[:8]} ", style="bold")
        left.append(f"shard {room.db} · {room.asta_mode}/{room.raise_mode} · ", style="dim")
        left.append(f"{room.seat.team_name or room.seat.fantateam_id}  ", style="cyan")
    left.append(f"credits {frame.credits_left}  ", style="bold")
    left.append(f"MAX {frame.max_cap}", style="dim")

    badge = (
        Text(" ● ARMED — REAL CREDITS ", style="bold white on red")
        if armed
        else Text(" DRY RUN ", style="dim")
    )
    return Panel(Group(left, badge), border_style="red" if armed else "dim")


def _lot_pane(frame: RoomFrame) -> RenderableType:
    """The player on the block, big, with the number that decides everything beside him."""
    if frame.lot_id is None:
        return Panel(Text("waiting for a lot", style="dim"), title="LOT", border_style="dim")

    body = Text()
    body.append(f"{frame.lot_name or frame.lot_id}\n", style="bold")
    body.append(f"price {frame.price} → next {frame.price + 1}   ", style="")
    body.append(f"[{frame.node}]\n", style="dim")
    if frame.high_bidder:
        body.append(f"held by {frame.high_bidder}\n", style="dim")

    if frame.seconds_left is None:
        body.append("timer --\n", style="dim")
    else:
        body.append(f"{frame.seconds_left:.1f}s\n", style="")

    if frame.walk_away is not None:
        # Provenance beside the number, never fused into it: a walk-away nobody can argue
        # with is a walk-away nobody can correct at 21:47.
        body.append(f"walk-away {frame.walk_away} ", style="bold")
        body.append(f"({frame.provenance})\n", style="dim")

    style = "red" if (frame.seconds_left or 99) < URGENT_SECONDS else "yellow" if frame.decision == "bid" else "white"
    return Panel(body, title="LOT", border_style=style)


def _model_pane(frame: RoomFrame) -> RenderableType:
    """What the model decided, and which guard bound when it decided against."""
    body = Text()
    body.append(f"{frame.decision.upper()}\n", style="bold")
    if frame.reason:
        body.append(f"refused by: {frame.reason}\n", style="dim")
    if frame.note:
        body.append(f"{frame.note}\n", style="yellow")
    body.append(f"owned {len(frame.owned)} · plan {len(frame.plan)}\n", style="dim")
    if frame.unresolved_sales:
        body.append(f"{frame.unresolved_sales} sale(s) we could not name\n", style="yellow")
    return Panel(body, title="MODEL", border_style="dim")


def _listone_pane(rows: list[ListoneRow]) -> RenderableType:
    """The whole listone the operator asked for — `docs/fantalab/00`'s "tutti i player"."""
    table = Table(expand=True, show_edge=False, pad_edge=False)
    for column, justify in (
        ("player", "left"), ("club", "left"), ("roles", "left"),
        ("value", "right"), ("price", "right"), ("walk", "right"), ("", "left"),
    ):
        table.add_column(column, justify=justify)  # type: ignore[arg-type]

    for row in rows:
        marker = {"ours": "ours", "taken": "sold", "open": ""}[row.status]
        style = {"ours": "green", "taken": "dim", "open": ""}[row.status]
        table.add_row(
            row.name, row.team, "/".join(row.roles),
            f"{row.value:.1f}", str(row.price),
            "" if row.walk_away is None else str(row.walk_away),
            marker, style=style,
        )
    return Panel(table, title="LISTONE", border_style="dim")


def _copilot_pane(advice: Commentary | None, *, offline: bool) -> RenderableType:
    """The second reading, or a box that says there is none.

    Its own pane, never woven into the LOT: the engine's number renders at t=0 whatever the
    network is doing, and a commentary that shared a box with it could not fail without
    taking the number's layout with it.

    `offline` and "nothing yet for this player" are kept apart. The first is a fact about the
    copilot worth acting on; the second is the ordinary case two seconds after a lot opens.
    """
    if offline:
        return Panel(Text("copilota: offline", style="dim"), title="COPILOTA", border_style="dim")
    if advice is None:
        return Panel(Text("...", style="dim"), title="COPILOTA", border_style="dim")

    body = Text()
    body.append(f"{advice.headline}\n", style="bold" if advice.disagrees_with_plan else "")
    body.append(f"{advice.why}\n", style="dim")
    for risk in advice.risks:
        body.append(f"  ! {risk}\n", style="yellow")
    for watch in advice.watch:
        body.append(f"  > {watch}\n", style="dim")
    body.append(f"[{advice.confidence}]", style="dim")

    # Amber only when it disagrees *and* is sure enough to be worth reading. A low-confidence
    # disagreement is the model saying "ignore me", and colouring it would train the operator
    # to ignore the colour instead.
    border = "yellow" if advice.disagrees_with_plan and advice.confidence != "low" else "dim"
    return Panel(body, title="COPILOTA", border_style=border)


def render(
    frame: RoomFrame,
    rows: list[ListoneRow],
    *,
    room: ResolvedRoom | None = None,
    armed: bool = False,
    advice: Commentary | None = None,
    copilot_offline: bool = False,
) -> RenderableType:
    """The whole screen for one frame. Total: no I/O, and no branch that can raise."""
    return Group(
        _header(frame, room, armed=armed),
        _lot_pane(frame),
        _model_pane(frame),
        _copilot_pane(advice, offline=copilot_offline),
        _listone_pane(rows),
    )
