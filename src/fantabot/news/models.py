"""The query contract: what one agent query must return.

:class:`PlayerSentiment` *is* the json_schema handed to the SDK. Its field
descriptions are prompt surface, not documentation — the model reads them to
decide what each number means — so they are written in Italian, addressed to the
model, and kept concrete.

Ranges are enforced here as well as in the schema. A model returning 1.4 is a
**failed query**, not a clamped row: a clamp hides a misread prompt behind a
plausible-looking number that then propagates into an auction bid.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PlayerSentiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentiment: float = Field(
        ge=-1.0,
        le=1.0,
        description="Outlook fantacalcistico complessivo: -1 pessimo, 0 neutro, +1 ottimo.",
    )
    disponibilita: float = Field(
        ge=0.0,
        le=1.0,
        description="0 = infortunato o squalificato, 1 = pienamente disponibile.",
    )
    titolarita: float = Field(
        ge=0.0,
        le=1.0,
        description="Probabilita che parta titolare nella prossima giornata.",
    )
    mercato: float = Field(
        ge=-1.0,
        le=1.0,
        description=(
            "-1 se in uscita o oscurato da un nuovo acquisto, +1 se in arrivo "
            "o con il ruolo rafforzato, 0 se non ci sono voci."
        ),
    )
    forma: float = Field(
        ge=-1.0, le=1.0, description="Forma recente: -1 in crisi, +1 in grande forma."
    )
    rigorista: float = Field(
        ge=0.0,
        le=1.0,
        description="Probabilita che sia il rigorista designato della squadra.",
    )
    piazzati: float = Field(
        ge=0.0,
        le=1.0,
        description="Probabilita che batta i calci piazzati (corner, punizioni).",
    )
    confidenza: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Quanto e solida l'evidenza raccolta. Metti 0 se non hai trovato "
            "nessuna notizia rilevante: e la risposta corretta, non un ripiego."
        ),
    )
    # 600, not 400. At 400 a live sample of 9 players came back at 336-399 chars
    # with four at 380+ — nothing truncated, but the model was visibly compressing
    # to fit, and detail is what these summaries are for. The cap is a backstop
    # against a runaway cell, not a style guide.
    riassunto: str = Field(
        max_length=600,
        description=(
            "Un paragrafo in italiano, solo fatti con le date. Niente ipotesi, "
            "niente frasi di circostanza."
        ),
    )
    fonti: list[str] = Field(
        default_factory=list,
        description="Solo gli URL che hai letto davvero. Lista vuota se nessuno.",
    )
    # The Mantra half. The platform freezes role tags in late July and never
    # revisits them (rules/sistema-mantra.md), so this is the one statistic no
    # file in data/ can hold. An empty list means the sources said nothing about
    # his position — see news/mantra.py for why that is not the same as
    # confirming the tag.
    ruolo_campo: list[str] = Field(
        default_factory=list,
        description=(
            "Codici ruolo Mantra effettivamente ricoperti nelle ultime partite, "
            "fra: Por Dc B Dd Ds E M C T W A Pc. Lista vuota se le fonti non "
            "dicono nulla sulla posizione in campo."
        ),
    )
