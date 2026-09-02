"""Option groups declared by more than one command.

Four options — `--season`, `--sentiment/--no-sentiment`, `--sentiment-run` and
`--tilt-k` — were declared once per command that takes them: thirteen declarations
across four commands, since `asta legality` carries a `--season` too. That is
thirteen places for a default or a help string to drift, and one of them already
had: the same flag was documented three different ways.

**The help text is now one wording per flag, which changes two commands' `--help`.**
That is a deliberate change and it is the point — the previous state was not three
descriptions chosen for three audiences, it was one description copied and then
edited in place. The surviving wording is the more informative of each pair:
`--no-sentiment` is described as the ablation control (which is what CLAUDE.md calls
it) rather than merely "on by default", and `--tilt-k 0` says what it falls back to.

Declared as `Annotated` aliases rather than shared `typer.Option` instances. A
`typer.Option` object carries per-parameter state, so reusing one instance across
signatures is sharing mutable state between commands; an `Annotated` alias is a type,
and Typer builds a fresh parameter from it at each use.
"""

from typing import Annotated

import typer

#: The only listone the asta engine plans against.
SEASON = "2026/27"

#: Defaults deliberately live in the command signatures, not here: `--tilt-k`'s is
#: `SentimentWeights().k`, and restating it would recreate the drift this module removes.

Season = Annotated[
    str,
    typer.Option("--season", help="Which stagione's Mantra listone."),
]

Sentiment = Annotated[
    bool,
    typer.Option(
        "--sentiment/--no-sentiment",
        help="Adjust values by the news feed. --no-sentiment is the fvm-only ablation.",
    ),
]

SentimentRun = Annotated[
    str,
    typer.Option(
        "--sentiment-run",
        help="Pin sentiment to one data_run (YYYY-MM-DD); default is each player's newest.",
    ),
]

TiltK = Annotated[
    float,
    typer.Option(
        "--tilt-k",
        min=0.0,
        max=1.0,
        help="Strength of the quality tilt. 0 uses the playing-time gate alone.",
    ),
]

#: The walk-away floor, as a fraction of a player's observed clearing price.
#:
#: The marginal walk-away collapses to zero over a pool of substitutes -- 10 of 30 measured
#: on the live database -- and `decide_bid` refuses at every price when it is zero, so
#: without a floor the bot refuses nearly everything it planned to buy.
#:
#: **0.0 is the ablation, not a disabled floor.** `price_floor` still clamps to the 1-credit
#: minimum bid, because a floor under 1 truncates to 0 and removes the player from the
#: biddable set entirely rather than merely pricing him low.
#:
#: **1.00, chosen against the corpus and against arithmetic.** `asta calibrate` is monotone in
#: alpha with no knee — spend, slots, schemi and `won %` all improve up to 1.0 and the corpus
#: cannot speak beyond it. And the plan is built to cost exactly the budget at `planning_cost`
#: (measured: 500 of 500), so `floor = 1.0 * planning_cost` makes the bidder's ceiling agree
#: with the plan's own budget. At 0.8 the floor would cap us at 400 for a plan we priced at
#: 500 — underbidding our own plan by construction. The MAX cap is what stops any single lot
#: taking more than its share; the floor is not the place to be timid.
FloorAlpha = Annotated[
    float,
    typer.Option(
        "--floor-alpha",
        min=0.0,
        help="Walk-away floor as a fraction of the observed clearing price.",
    ),
]


#: How far under a player's observed clearing price a lot the plan did *not* pick has to sit
#: before the room takes it anyway. `0` disables the opportunistic path entirely and restores
#: the plan-only behaviour. See `domain/asta/reservation.BARGAIN_BETA` for why 0.60.
BargainBeta = Annotated[
    float,
    typer.Option(
        "--bargain-beta",
        min=0.0,
        max=1.0,
        help="Take an unplanned lot under this fraction of its book price; 0 disables.",
    ),
]


#: The aggregate cap. Each bargain is judged against the plan on its own, and "better than the
#: plan" does not compose — several of them approved one at a time is a drained purse nothing
#: else in the loop would notice. A fraction of the *starting* budget, so the limit cannot
#: re-earn itself as the evening spends. See `domain/asta/reservation.BARGAIN_BUDGET_SHARE`.
BargainShare = Annotated[
    float,
    typer.Option(
        "--bargain-share",
        min=0.0,
        max=1.0,
        help="Cap on total unplanned spend, as a fraction of the starting budget.",
    ),
]
