"""Run the two collector queries and gate the results. No I/O beyond the queries.

Gating happens here rather than in the CLI so the "writing nothing" decision is
one testable function instead of a branch buried in a command. The caller writes
only when :attr:`CollectResult.problems` is empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from ..agentkit.options import AgentRequest
from ..agentkit.runner import Outcome
from ..agentkit.runner import run as sdk_run
from .gates import check_compat, check_schemi
from .models import CompatMatrix, SchemaGrid
from .prompt import COMPAT_PROMPT, SCHEMI_PROMPT

M = TypeVar("M", bound=BaseModel)

# Higher than the news queries: this one has to find a downloadable table, not
# read a handful of articles.
MAX_TURNS = 25


class CollectError(RuntimeError):
    """A collector query came back without a usable answer."""


@dataclass(frozen=True)
class CollectResult:
    grid: SchemaGrid
    matrix: CompatMatrix
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


async def collect(model: str) -> CollectResult:
    grid = await _ask(SCHEMI_PROMPT, "schemi", model, SchemaGrid)
    matrix = await _ask(COMPAT_PROMPT, "compat", model, CompatMatrix)
    return CollectResult(
        grid=grid,
        matrix=matrix,
        problems=check_schemi(grid) + check_compat(matrix, grid),
    )


async def _ask(prompt: str, label: str, model: str, schema: type[M]) -> M:
    request = AgentRequest(
        prompt=prompt,
        label=label,
        model=model,
        allowed_tools=("WebSearch", "WebFetch"),
        max_turns=MAX_TURNS,
    )
    outcome: Outcome[M] = await sdk_run(request, schema)
    if outcome.value is None:
        raise CollectError(f"{label}: {outcome.failure or 'no value returned'}")
    return outcome.value
