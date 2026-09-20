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

import math
from typing import Annotated

import typer

#: The only listone the asta engine plans against.
SEASON = "2026/27"


def _reject_non_finite(value: float) -> float:
    """Typer/Click callback: refuse ``nan`` and ``inf`` before any cycle runs.

    ``click.FloatRange`` compares with ``<``/``>``, and every comparison against ``nan`` is
    ``False`` — a ``FloatRange(0.0, 1.0)`` accepts ``nan`` for the exact reason it looks like it
    should reject it, and a lower-bound-only range (``min=`` with no ``max=``) lets ``inf``
    through too, since ``inf`` is never less than the minimum.

    Measured live: ``--bargain-share nan`` reaches ``RoomTracker.cycle`` at the shipped
    *disabled* default (``--bargain-beta 0.00``) and raises ``ValueError: cannot convert float
    NaN to integer`` from inside a poll, because the allowance arithmetic runs unconditionally
    every cycle. Click's own type conversion (the range check) runs before this callback, so
    this only has to close the gap that check cannot see — a value already rejected by
    ``min=``/``max=`` never reaches here.
    """
    if not math.isfinite(value):
        raise typer.BadParameter("must be a finite number")
    return value

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
        callback=_reject_non_finite,
        help="Strength of the quality tilt. 0 uses the playing-time gate alone.",
    ),
]

#: A premium (or discount) on `lot_ceiling`'s own re-solved number. Replaces the walk-away
#: floor this used to be (Task 1.3): that floor scaled a player's *book price*, and it existed
#: because the marginal walk-away it was patching collapsed to zero over a pool of
#: substitutes — 10 of 30 measured on the live database. `lot_ceiling` re-solves honestly
#: instead of approximating, so there is no bare-zero collapse left to patch; this multiplier
#: is the operator's premium on top of an already-real number, not a fix for a broken one.
#:
#: **1.00 by default — no premium.** `asta calibrate` replays this against 45 recorded 8x500
#: rooms. On 2026-08-28/29, 1.00 lost the first four live contests by 1-14 credits; switching
#: mid-auction to 1.15 won 7 of the next 10 — evidence the honest ceiling still under-bids by a
#: measurable margin against real rivals, not a value tuned against the corpus in advance. The
#: MAX cap (`hard_cap`, `bid.max_bid`) is what stops any single lot taking more than its share;
#: this knob is not the place to be timid about a lot the ceiling already approved.
CeilingAlpha = Annotated[
    float,
    typer.Option(
        "--ceiling-alpha",
        min=0.0,
        callback=_reject_non_finite,
        help="Premium on lot_ceiling's own number (1.00 = none).",
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
        callback=_reject_non_finite,
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
        callback=_reject_non_finite,
        help="Cap on total unplanned spend, as a fraction of the starting budget.",
    ),
]


#: The recorded league shape a plan is priced against. Not the same thing as `--budget`,
#: which is *our* remaining credits: this addresses a cell of the corpus.
#:
#: **Every planning command states it now**, because five of six did not. The default was
#: 8x500 inside `read_plan_inputs`, which is our room, so nothing looked wrong — and
#: `docs/fantalab/00 §13` is explicit that a league rule written into the code is a bug,
#: the next asta being the riparazione in January or a friend's league. `asta calibrate`
#: was the case that proved it: it forwarded `--teams/--credits` to the *replay* corpus and
#: called `read_plan_inputs` with no shape at all, grading a 10x1000 corpus against prices
#: averaged from 8x500 rooms.
CorpusTeams = Annotated[
    int,
    typer.Option(
        "--teams",
        min=2,
        help="Recorded league shape: number of teams. The corpus is filtered to it, so "
        "prices need no budget normalization.",
    ),
]

CorpusCredits = Annotated[
    int,
    typer.Option(
        "--credits",
        min=1,
        help="Recorded league shape: credits per team. An unrecorded shape is refused, "
        "never priced off the nearest one.",
    ),
]


#: The same two options for a command that can **ask** — `asta live --league` names a
#: FantaLab room whose `RoomConfig` states its own `num_teams`/`num_credits`.
#:
#: `int | None`, and the `None` is the whole point: Typer hands a body the default and a
#: typed value indistinguishably, so with `int` there is no way to tell `--teams 8` from a
#: run that said nothing — and "said nothing" is what has to defer to the room. The four
#: commands with no room to ask keep the plain `int` above; a sentinel they could never act
#: on would be an option that lies about what it does.
#:
#: Not caught by `NoCorpus`: 8x500 is the corpus's biggest cell, so a 10x650 room left at
#: the default lands on a cell that exists and is full, and is priced against somebody
#: else's league in silence.
DetectedCorpusTeams = Annotated[
    int | None,
    typer.Option(
        "--teams",
        min=2,
        help="Recorded league shape: number of teams. Read from the room on --league; "
        "stating it overrides what the room says, and says so.",
    ),
]

DetectedCorpusCredits = Annotated[
    int | None,
    typer.Option(
        "--credits",
        min=1,
        help="Recorded league shape: credits per team. Read from the room on --league; "
        "an unrecorded shape is refused, never priced off the nearest one.",
    ),
]



#: The fantacalcio lega whose roster band (and format) to plan on.
#:
#: **Named `--lega`, not `--league`, and the collision is why.** `asta bid --league` is the
#: *FantaLab* room id, a uuid-ish string; `lega show --league` is the fantacalcio lega id, an
#: integer. Two different identifiers already wear that flag on two commands, so a third
#: meaning on a third would be the footgun rather than the fix.
#:
#: `0` means "read `FANTABOT_LEAGUE_ID`", and an unset one means "plan on the default band",
#: which is what these commands did before 2.1 — so the flag adds a capability and removes
#: nothing.
Lega = Annotated[
    int,
    typer.Option(
        "--lega",
        min=0,
        help="Fantacalcio lega id: plan on ITS roster band and format, from the last "
        "`lega sync`. Defaults to FANTABOT_LEAGUE_ID; 0 plans on the built-in band.",
    ),
]
