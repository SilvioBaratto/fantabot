"""The per-player prompt. Pure: player plus window in, string out.

Two things here are deliberate and easy to undo by accident.

**The run date is a parameter, not ``date.today()``.** Determinism is what lets
the wording be pinned by a test, and what makes a resumed run produce the same
question it would have produced an hour earlier.

**Search is steered to a limited, high-value set.** ``PREFERRED_DOMAINS`` is a
handful of fetchable fantacalcio sources (probabili formazioni, injuries, mercato);
the model searches those first and falls back to a club's own site only when none of
them cover an obscure player. Narrowing the set is a cost lever — fewer, better
fetches — and gazzetta.it is out because Anthropic's crawler cannot fetch it. The
``fonti`` field records what was actually read, so source drift shows up in the data.
"""

from __future__ import annotations

from datetime import date, timedelta

from fantabot.news.pool import PoolPlayer

# A deliberately small set of high-value, *fetchable* fantacalcio sources: probabili
# formazioni, injury/suspension lists with recovery times, and mercato. Narrowing the
# search here is a cost lever — fewer, better fetches per player. gazzetta.it is out:
# Anthropic's WebFetch crawler cannot read it (HTTP 400), so every visit was a wasted
# turn. The club's own site stays as a prose escape in the prompt, for the obscure
# players the aggregators ignore.
PREFERRED_DOMAINS: tuple[str, ...] = (
    "fantacalcio.it",
    "fantamaster.it",
    "fantacalciopedia.com",
    "tuttomercatoweb.com",
    "sport.sky.it",
)

# The twelve Mantra codes in the rules doc's own casing, so the model answers in a
# form parse_codes() accepts without guessing at capitalisation.
_MANTRA_LEGEND = (
    "Por (portiere), Dc (difensore centrale), B (braccetto), Dd (terzino destro), "
    "Ds (terzino sinistro), E (esterno difensivo), M (mediano), C (centrocampista "
    "centrale), T (trequartista), W (ala), A (attaccante di raccordo), Pc (punta centrale)"
)


def build_system_prompt(lookback_days: int, today: date) -> str:
    """The stable half of the prompt: the analyst brief, the window, the rules.

    It carries no player, so within a run (one ``lookback_days``/``today``) it is one
    constant string for all 523 queries. The pipeline sends it as the agent's system
    prompt, where an identical prefix is cached and read back at a reduced rate rather
    than re-billed as fresh input on every player. The window lives here, not in the
    per-player message, because it too is constant across a run.
    """
    since = today - timedelta(days=lookback_days)
    domains = ", ".join(PREFERRED_DOMAINS)

    return f"""Sei un analista di fantacalcio. Per ogni calciatore di Serie A che ti viene indicato, raccogli notizie e restituisci una valutazione strutturata.

PERIODO
Considera solo notizie pubblicate negli ultimi {lookback_days} giorni, cioè dal {since.isoformat()} al {today.isoformat()}.

FONTI
Cerca PRINCIPALMENTE in questo elenco limitato di fonti ad alto valore: {domains}. Sono le più affidabili per probabili formazioni, infortuni e squalifiche. Vai altrove (per esempio il sito ufficiale del club) SOLO se nessuna di queste copre il giocatore.
Fai al massimo 2 ricerche e leggi al massimo 4 fonti: non aprire più pagine del necessario. In `fonti` metti solo gli URL che hai letto davvero: non elencare risultati di ricerca che non hai aperto.

COME PESARE LE NOTIZIE
- Le notizie degli ultimi 3 giorni contano più di quelle di 10 giorni fa.
- Una vicenda già risolta (infortunio rientrato, mercato chiuso, squalifica scontata) non deve continuare ad abbassare i punteggi: descrivila come chiusa.
- In `riassunto` cita sempre la data dei fatti, così si distingue questa settimana dalla precedente.

RUOLO IN CAMPO
La piattaforma assegna i ruoli Mantra a fine luglio e non li aggiorna più per tutta la stagione. Dimmi in quale ruolo Mantra lo stanno effettivamente schierando nelle ultime partite, secondo probabili formazioni e cronache.
Codici ammessi: {_MANTRA_LEGEND}.
Metti i codici osservati in `ruolo_campo`. Se le fonti non parlano della sua posizione in campo, lascia `ruolo_campo` vuoto: non dedurlo dal ruolo assegnato per il giocatore.

SE NON TROVI NULLA
Metti `confidenza` a 0, `fonti` vuoto, `ruolo_campo` vuoto e `riassunto` "Nessuna notizia rilevante nel periodo.". È la risposta corretta: non inventare un sentiment per un giocatore di cui nessuno ha scritto.
"""


def build_user_prompt(player: PoolPlayer) -> str:
    """The variable half: just this player, and the instruction to analyse him.

    Everything constant across the run lives in the system prompt; keeping the user
    message this small is what lets the split pay off — the per-player tokens are only
    the four identity lines, not the whole brief.
    """
    return f"""GIOCATORE
- Nome: {player.nome}
- Squadra: {player.squadra}
- Ruolo Classic: {player.ruolo}
- Ruolo Mantra attualmente assegnato dalla piattaforma: {player.ruoli_mantra}

Analizza questo giocatore e restituisci la valutazione strutturata richiesta."""


def build_prompt(player: PoolPlayer, lookback_days: int, today: date) -> str:
    """The whole prompt as one string. Kept for ``--print-prompt`` and the tests; the
    pipeline sends the two halves separately so the system half can cache."""
    return f"{build_system_prompt(lookback_days, today)}\n\n{build_user_prompt(player)}"
