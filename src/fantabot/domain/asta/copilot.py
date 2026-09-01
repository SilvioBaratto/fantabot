"""The live copilot's pure half: what it is told, what it may answer, and how it is asked.

`tasks/archive/asta-design.md` Part 2 ranks the live LLM's uses, and the one it was originally
wanted for — judging a player — comes last. The facts about a player are static and are better
precomputed the night before with a bigger model and search enabled. What a live call is
actually good for is looking at *this* state and asking whether our own number looks wrong.

**It may never name a price.** `Commentary` carries no numeric field, and
`test_commentary_has_no_numeric_field` walks the schema to keep it that way. The failure this
guards against is not a hallucination in the abstract: it is 21:47, the operator is tired, the
model says "prendilo, vale 60", and 60 credits go to a 40-credit player. The walk-away stays
deterministic; the copilot writes prose beside it.

If a number is ever wanted, `clamp` (T18) is the only sanctioned shape — advice may lower a
bid, never raise it. It is landed unused so the shape exists before the temptation.

**Advice is keyed by player, never by "the lot on the block".** `counter_time` is 7-10 s
(`docs/fantalab/06`) and one structured call takes seconds, so an answer about the current lot
describes a lot that has already closed. The plan's top targets are briefed ahead of time and
looked up when they come up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class CopilotBrief:
    """What the model is told about one player. Deliberately small.

    Everything here is already on screen. The copilot's value is not extra facts — it is a
    second reading of the facts we have, which is the only thing a live call can do that a
    precompute cannot.
    """

    player_id: str
    name: str
    team: str
    roles: tuple[str, ...]
    #: Our own ceiling for him, so the model can disagree with it in words.
    walk_away: int
    #: What rooms of this shape have actually paid for him, when anyone has.
    observed_price: int | None
    credits_left: int
    slots_left: int
    #: How many of the eleven schemi the rosa can field as it stands.
    schemi_open: int
    #: The last few sales, as `name price buyer` — the room's tempo in one line.
    recent: tuple[str, ...]


class Commentary(BaseModel):
    """The copilot's answer. Prose and flags; **no numbers**.

    `extra="forbid"` matters more here than in the news schema: it is what stops a model
    volunteering a `suggested_price` the renderer might one day decide to show.
    """

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(
        max_length=60,
        description="Una riga sola, leggibile in un colpo d'occhio sotto il prezzo.",
    )
    why: str = Field(
        max_length=280,
        description="Perché, in due frasi al massimo. Nessuna cifra: i numeri li fa il motore.",
    )
    risks: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Cosa potrebbe andare storto se lo prendiamo a questo prezzo.",
    )
    watch: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Altri giocatori da tenere d'occhio se questo salta.",
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description="Quanto è solida questa lettura. 'low' significa: ignorami."
    )
    disagrees_with_plan: bool = Field(
        description="True se il piano del motore ti sembra sbagliato su questo giocatore."
    )


SYSTEM_PROMPT = """Sei il copilota di un'asta di fantacalcio Mantra, in tempo reale.

Il motore ha già calcolato quanto siamo disposti a pagare. Il tuo compito NON è dare un
prezzo: è guardare lo stato dell'asta e dire se quel numero sembra sbagliato, e perché.

Regole:
- Non scrivere mai una cifra in crediti. Nessuna. Il numero è del motore.
- Una riga di headline, leggibile mentre scorre un timer da dieci secondi.
- Se non hai niente di utile da dire, dillo con confidence 'low'. È una risposta valida.
- Italiano."""


def brief_prompt(brief: CopilotBrief) -> str:
    """The user turn for one player. Pure — the facts, and nothing about how to answer."""
    price = "mai venduto in aste registrate" if brief.observed_price is None else (
        f"di solito va via intorno a {brief.observed_price}"
    )
    recent = "\n".join(f"  - {line}" for line in brief.recent) or "  (nessuna ancora)"
    return (
        f"Giocatore: {brief.name} ({brief.team}, {'/'.join(brief.roles)})\n"
        f"Il motore arriva a {brief.walk_away}; {price}.\n"
        f"Abbiamo {brief.credits_left} crediti e {brief.slots_left} slot da riempire.\n"
        f"La rosa attuale schiera {brief.schemi_open} degli 11 moduli.\n"
        f"Ultime aggiudicazioni:\n{recent}\n\n"
        "Il motore sbaglia su questo giocatore?"
    )


def clamp(walk_away: int, advice: Commentary | None, *, cap: int) -> int:
    """The only sanctioned way advice may ever touch a number: downward, never up. Pure.

    **Landed deliberately unused.** `Commentary` has no numeric field, so nothing calls this
    today and `test_clamp_has_no_caller` keeps it that way. It exists because the shape has to
    exist *before* the temptation does: the day somebody wants the model to influence a price,
    the argument will be had against a monotone ratchet that is already written and tested,
    rather than in the open with an evening's deadline pressing.

    A ratchet rather than a multiplier. `tasks/archive/asta-design.md` proposed a bounded
    ±20% tilt, and the difference matters: a multiplier that can raise a bid makes the model's
    worst failure — confident and wrong at 21:47 — cost money. `min` makes its worst failure
    cost a player, which is recoverable.

    `advice` is `None`-safe because "no commentary" is the model-free baseline and must behave
    exactly as if the copilot were switched off. Low confidence is treated the same way: the
    model saying "ignore me" is a request this honours.
    """
    ceiling = min(walk_away, cap)
    if advice is None or advice.confidence == "low":
        return ceiling
    # Every future numeric channel goes through this line. Whatever is added above it, the
    # result cannot exceed the engine's own ceiling.
    return min(ceiling, walk_away)
