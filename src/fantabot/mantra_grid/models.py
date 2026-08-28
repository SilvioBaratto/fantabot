"""The shapes the collector must return. These are the json_schema it is held to."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MantraSchema(BaseModel):
    """One tactical schema: a name and its ten outfield slots.

    The goalkeeper is fixed and sits outside the four lines, so it is not a slot.
    A slot listing several codes means they are interchangeable alternatives —
    and stay interchangeable through substitutions. Usually two; 4-3-1-2 has one
    slot of three (``T/A/Pc``), which a gate asserting a ceiling of two had
    silently truncated to ``A/Pc`` until the published table was read directly.
    """

    model_config = ConfigDict(extra="forbid")

    nome: str = Field(description="Nome dello schema, es. 4-3-3")
    slots: list[list[str]] = Field(
        description=(
            "I 10 slot di movimento in ordine di linea (difesa, centrocampo, "
            "trequarti, attacco). Ogni slot e una lista di codici ruolo "
            "intercambiabili. Il portiere non e uno slot."
        )
    )


class SchemaGrid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemi: list[MantraSchema] = Field(description="Gli 11 schemi tattici del sistema Mantra.")


#: The four things a cell can say. ``-1*`` is the load-bearing one: it means the
#: platform refuses the placement *at lineup submission* and allows it only as
#: the outcome of a forced substitution. A matrix that collapses it into ``-1``
#: reads as "allowed with a malus" and produces lineups the site rejects.
CELL_VALUES: frozenset[str] = frozenset({"ok", "-1", "-1*", "no"})

#: Column order of every row, and the 12 Mantra role codes.
ROLE_ORDER: tuple[str, ...] = ("Por", "Dd", "Ds", "Dc", "B", "E", "M", "C", "T", "W", "A", "Pc")


class SlotCompat(BaseModel):
    """One slot of one schema, and what each of the 12 roles may do in it."""

    model_config = ConfigDict(extra="forbid")

    slot: str = Field(description="Lo slot come lo stampa la tabella, es. 'Dc/B' o 'T/A/Pc'.")
    compat: list[str] = Field(
        description=(
            "Un valore per ciascuno dei 12 ruoli, nell'ordine di `ruoli`: "
            "'ok', '-1', '-1*' oppure 'no'."
        )
    )


class FormationCompat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_nome: str = Field(description="Lo schema a cui si riferisce, es. 4-1-4-1")
    slots: list[SlotCompat] = Field(
        description="Una riga per slot: il portiere piu i 10 di movimento."
    )


class CompatMatrix(BaseModel):
    """The full per-formation table, not a list of exceptions to it.

    The first version of this model stored only ``vietati`` — the pairs a schema
    forbids outright — and the file it produced held a single entry, the one
    exception the prompt had already named. That is a true statement about the
    table and a useless one for deciding whether a rosa can field a schema, which
    needs every cell and, above all, the ``-1*`` ones.
    """

    model_config = ConfigDict(extra="forbid")

    ruoli: list[str] = Field(
        default_factory=lambda: list(ROLE_ORDER),
        description="I 12 codici ruolo, nell'ordine delle colonne.",
    )
    legenda: dict[str, str] = Field(
        default_factory=dict, description="Cosa significa ciascun valore di cella."
    )
    formazioni: list[FormationCompat] = Field(description="Una voce per ciascuno degli 11 schemi.")
    # Recording what was actually read is the only way to tell a real collection
    # from the prompt's own worked example handed straight back.
    fonti: list[str] = Field(
        default_factory=list,
        description="Gli URL che hai letto davvero per compilare la tabella.",
    )
