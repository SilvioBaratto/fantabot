"""Pure helpers for the offline asta CLI: parse input, assemble inputs, render output.

Kept out of ``cli.py`` so the parsing, the naive-value assembly and the rendering are unit
-testable without a database. The CLI is the thin I/O shell that fetches rows and calls these.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from fantabot.asta_engine.roles import MantraPlayer, normalize_roles
from fantabot.asta_engine.sentiment import SentimentWeights, effect_by_id, variance_by_id
from fantabot.asta_engine.state import Roster
from fantabot.asta_engine.value import NaiveValueModel
from fantabot.data_sources.models import SentimentRow


def parse_ids(raw: str) -> tuple[str, ...]:
    """Split a ``--owned``/``--rosa`` string on commas and whitespace, dropping blanks."""
    return tuple(token for token in re.split(r"[,\s]+", raw.strip()) if token)


def parse_replay_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    """JSON-decode replay lines, skipping blanks and anything malformed.

    A live capture is not guaranteed clean — one garbled line must not abort the replay.
    """
    out: list[dict[str, Any]] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            out.append(decoded)
    return out


def build_pool(roles_by_id: Mapping[str, Sequence[str]]) -> list[MantraPlayer]:
    """Build the player pool from per-player role codes."""
    return [
        MantraPlayer(id=player_id, roles=normalize_roles(roles))
        for player_id, roles in roles_by_id.items()
    ]


def build_value(
    fvm_by_id: Mapping[str, float],
    priced_ids: set[str],
    *,
    sentiment: Mapping[str, SentimentRow] | None = None,
    as_of: date | None = None,
    weights: SentimentWeights = SentimentWeights(),
    prior_mean: float = 1.0,
    base_variance: float = 4.0,
    no_history_variance: float = 16.0,
) -> NaiveValueModel:
    """A naive value model from the market's fantavalore (``fvm``) as the value proxy.

    A player with an ``fvm`` but no sale in the loaded aste (``priced_ids``) is treated as
    no-history — same mean, a wider band — since the market has not settled a price on him.

    ``sentiment`` is optional and **defaults to off**, which is not politeness: omitting it
    has to reproduce the pre-sentiment model exactly, field for field, or ``--no-sentiment``
    is not an ablation control. When supplied, each ``fvm`` is scaled by that player's
    pool-normalized effect (see ``sentiment.py`` for why the pool mean is pinned at 1.0).

    Supplying it also gives every player his own variance, interpolated from
    ``base_variance`` at full confidence to ``no_history_variance`` at none — which is what
    makes ``lam`` do anything at all, since an identical band on every candidate cannot
    change which candidate wins.

    ``as_of`` is required alongside it and has no default. Defaulting it to today would put
    a clock read inside a pure function, and make the age decay depend on when the suite
    happened to run.
    """
    if sentiment is not None and as_of is None:
        raise ValueError("as_of is required when sentiment is supplied")

    signals = {player_id: float(value) for player_id, value in fvm_by_id.items()}
    variances: dict[str, float] = {}
    if sentiment is not None and as_of is not None:
        effects = effect_by_id(sentiment, signals, as_of=as_of, weights=weights)
        variances = variance_by_id(
            sentiment,
            signals,
            as_of=as_of,
            base=base_variance,
            widest=no_history_variance,
            weights=weights,
        )
        signals = {
            player_id: value * effects[player_id] for player_id, value in signals.items()
        }
    no_history = frozenset(player_id for player_id in fvm_by_id if player_id not in priced_ids)
    return NaiveValueModel(
        signals=signals,
        prior_mean=prior_mean,
        base_variance=base_variance,
        no_history_variance=no_history_variance,
        no_history=no_history,
        variances=variances,
    )


def format_roster(
    roster: Roster,
    names: Mapping[str, str],
    prices: Mapping[str, float],
    *,
    sentiment: Mapping[str, SentimentRow] | None = None,
) -> str:
    """One header line plus a line per player (name, expected price, and any role drift).

    The drift annotation is a **warning, not a permission**. The platform freezes Mantra
    role tags in late July and enforces its own at submission, so a player tagged ``A`` who
    is being played as ``W`` is still fielded as an ``A``. Printing it here is the whole of
    what the engine does with drift, besides widening his band: surfaced for the operator to
    weigh, never fed back into legality.
    """
    lines = [
        f"roster: {len(roster)} players | cost {roster.total_cost:.0f} | obj {roster.objective:.1f}"
    ]
    for player_id in roster.player_ids:
        row = (sentiment or {}).get(player_id)
        drift = ""
        if row is not None and row.deriva_ruolo > 0:
            drift = f"  ⚠ tagged {row.ruoli_mantra} / played {row.ruolo_campo}"
        lines.append(
            f"  {names.get(player_id, player_id):<24} {prices.get(player_id, 0.0):>5.0f}{drift}"
        )
    return "\n".join(lines)


def format_legality(schemi: frozenset[str]) -> str:
    """Render the set of fieldable schemi, or say none field."""
    if not schemi:
        return "fields NO legal XI"
    return "fields: " + ", ".join(sorted(schemi))
