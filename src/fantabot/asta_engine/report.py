"""Pure helpers for the offline asta CLI: parse input, assemble inputs, render output.

Kept out of ``cli.py`` so the parsing, the naive-value assembly and the rendering are unit
-testable without a database. The CLI is the thin I/O shell that fetches rows and calls these.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .roles import MantraPlayer, normalize_roles
from .state import Roster
from .value import NaiveValueModel


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
    prior_mean: float = 1.0,
    base_variance: float = 4.0,
    no_history_variance: float = 16.0,
) -> NaiveValueModel:
    """A naive value model from the market's fantavalore (``fvm``) as the value proxy.

    A player with an ``fvm`` but no sale in the loaded aste (``priced_ids``) is treated as
    no-history — same mean, a wider band — since the market has not settled a price on him.
    """
    signals = {player_id: float(value) for player_id, value in fvm_by_id.items()}
    no_history = frozenset(player_id for player_id in fvm_by_id if player_id not in priced_ids)
    return NaiveValueModel(
        signals=signals,
        prior_mean=prior_mean,
        base_variance=base_variance,
        no_history_variance=no_history_variance,
        no_history=no_history,
    )


def format_roster(roster: Roster, names: Mapping[str, str], prices: Mapping[str, float]) -> str:
    """One header line plus a line per player (name and expected price)."""
    lines = [
        f"roster: {len(roster)} players | cost {roster.total_cost:.0f} | obj {roster.objective:.1f}"
    ]
    for player_id in roster.player_ids:
        lines.append(f"  {names.get(player_id, player_id):<24} {prices.get(player_id, 0.0):>5.0f}")
    return "\n".join(lines)


def format_legality(schemi: frozenset[str]) -> str:
    """Render the set of fieldable schemi, or say none field."""
    if not schemi:
        return "fields NO legal XI"
    return "fields: " + ", ".join(sorted(schemi))
