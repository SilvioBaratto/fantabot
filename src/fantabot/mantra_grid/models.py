"""The shapes the collector must return. These are the json_schema it is held to."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MantraSchema(BaseModel):
    """One tactical schema: a name and its ten outfield slots.

    The goalkeeper is fixed and sits outside the four lines, so it is not a slot.
    A slot listing two codes means they are interchangeable alternatives — and
    stay interchangeable through substitutions.
    """

    model_config = ConfigDict(extra="forbid")

    nome: str = Field(description="Nome dello schema, es. 4-3-3")
    slots: list[list[str]] = Field(
        description=(
            "I 10 slot di movimento in ordine di linea (difesa, centrocampo, "
            "trequarti, attacco). Ogni slot e una lista di 1 o 2 codici ruolo "
            "intercambiabili. Il portiere non e uno slot."
        )
    )


class SchemaGrid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemi: list[MantraSchema] = Field(description="Gli 11 schemi tattici del sistema Mantra.")


class FormationCompat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_nome: str = Field(description="Lo schema a cui si riferisce, es. 4-1-4-1")
    vietati: list[list[str]] = Field(
        description=(
            "Coppie [da, a] di ruoli il cui scambio e impossibile in questo schema "
            "anche accettando il malus. Lista vuota se non ce ne sono."
        )
    )


class CompatMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formazioni: list[FormationCompat] = Field(description="Una voce per ciascuno degli 11 schemi.")
    # The compatibility table is published as a separate download this repo has no
    # local copy of. Recording what was actually read is the only way to tell a real
    # collection from the prompt's own worked example handed straight back.
    fonti: list[str] = Field(
        default_factory=list,
        description="Gli URL che hai letto davvero per compilare la tabella.",
    )
