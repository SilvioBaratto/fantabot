"""Fast state entry: turn a typed line like 'malen a luca 192' into a resolved sale.

Data entry is the real adoption risk — if logging a sale costs more than a few seconds the
tool stops being used mid-evening — so the live LLM parses the free text and a pure resolver
matches the fuzzy name against the listone. The schema and the resolver are pure; the async
agentkit call is a thin shell (the parse itself), verified on the real backend.

An ambiguous or unmatched name resolves to ``None`` — surfaced, never guessed. A wrong player
id entered under time pressure is worse than being asked to retype.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from ..agentkit.options import AgentRequest
from ..agentkit.runner import Outcome
from ..agentkit.runner import run as sdk_run


class StateEntry(BaseModel):
    """What the LLM extracts from the typed line. Field descriptions are prompt surface."""

    model_config = ConfigDict(extra="forbid")

    player: str = Field(min_length=1, description="Il nome del giocatore, come digitato.")
    price: int = Field(ge=1, description="Il prezzo in crediti a cui e stato aggiudicato.")
    team: str = Field(description="La squadra (fantallenatore) che lo ha preso.")


@dataclass(frozen=True)
class ResolvedEntry:
    """A `StateEntry` whose fuzzy player name has been matched to a listone id."""

    player_id: str
    price: int
    team: str


def build_prompt(text: str) -> str:
    """The per-entry prompt. Pure: the raw line in, a string out."""
    return (
        "Sei un registratore di aste di fantacalcio. L'utente digita una riga come "
        "'malen a luca 192', che significa: il giocatore Malen e stato aggiudicato al "
        "fantallenatore Luca per 192 crediti. Estrai nome giocatore, prezzo e squadra.\n\n"
        f"RIGA\n{text}"
    )


def resolve(entry: StateEntry, names_by_id: Mapping[str, str]) -> ResolvedEntry | None:
    """Match the fuzzy player name to exactly one listone id, or ``None`` if not unique. Pure."""
    query = entry.player.strip().lower()
    matches = [player_id for player_id, name in names_by_id.items() if query in name.lower()]
    if len(matches) != 1:
        return None  # zero or ambiguous — surfaced, never guessed
    return ResolvedEntry(player_id=matches[0], price=entry.price, team=entry.team)


Runner = Callable[[AgentRequest, type[StateEntry]], Awaitable[Outcome[StateEntry]]]


async def parse_entry(
    text: str,
    *,
    model: str,
    runner: Runner = sdk_run,
) -> Outcome[StateEntry]:
    """Parse one typed line via the agent. Thin shell; the runner is injected for testing."""
    request = AgentRequest(
        prompt=build_prompt(text),
        label="state-entry",
        model=model,
        allowed_tools=(),  # no search: the parse is offline
        max_turns=1,
    )
    return await runner(request, StateEntry)
