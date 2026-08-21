"""The per-player prompt. Pure: player plus window in, string out.

Two things here are deliberate and easy to undo by accident.

**The run date is a parameter, not ``date.today()``.** Determinism is what lets
the wording be pinned by a test, and what makes a resumed run produce the same
question it would have produced an hour earlier.

**Sources are preferred, not exclusive.** A club's own injury bulletin is
routinely the primary source, and the low-profile players where news matters most
are exactly the ones the big aggregators ignore. The ``fonti`` field records what
was actually read, so source drift shows up in the data instead of being assumed
away.
"""

from __future__ import annotations

from datetime import date, timedelta

from .pool import PoolPlayer

PREFERRED_DOMAINS: tuple[str, ...] = (
    "fantacalcio.it",
    "gazzetta.it",
    "tuttomercatoweb.com",
    "il sito ufficiale del club",
)

# The twelve Mantra codes in the rules doc's own casing, so the model answers in a
# form parse_codes() accepts without guessing at capitalisation.
_MANTRA_LEGEND = (
    "Por (portiere), Dc (difensore centrale), B (braccetto), Dd (terzino destro), "
    "Ds (terzino sinistro), E (esterno difensivo), M (mediano), C (centrocampista "
    "centrale), T (trequartista), W (ala), A (attaccante di raccordo), Pc (punta centrale)"
)


def build_prompt(player: PoolPlayer, lookback_days: int, today: date) -> str:
    since = today - timedelta(days=lookback_days)
    domains = ", ".join(PREFERRED_DOMAINS)

    return f"""Sei un analista di fantacalcio. Raccogli notizie su un singolo calciatore di Serie A e restituisci una valutazione strutturata.

GIOCATORE
- Nome: {player.nome}
- Squadra: {player.squadra}
- Ruolo Classic: {player.ruolo}
- Ruolo Mantra attualmente assegnato dalla piattaforma: {player.ruoli_mantra}

PERIODO
Considera solo notizie pubblicate negli ultimi {lookback_days} giorni, cioè dal {since.isoformat()} al {today.isoformat()}.

FONTI
Fonti preferite (non esclusive): {domains}.
Se queste non dicono nulla, cerca altrove. In `fonti` metti solo gli URL che hai letto davvero: non elencare risultati di ricerca che non hai aperto.

COME PESARE LE NOTIZIE
- Le notizie degli ultimi 3 giorni contano più di quelle di 10 giorni fa.
- Una vicenda già risolta (infortunio rientrato, mercato chiuso, squalifica scontata) non deve continuare ad abbassare i punteggi: descrivila come chiusa.
- In `riassunto` cita sempre la data dei fatti, così si distingue questa settimana dalla precedente.

RUOLO IN CAMPO
La piattaforma assegna i ruoli Mantra a fine luglio e non li aggiorna più per tutta la stagione. Dimmi in quale ruolo Mantra lo stanno effettivamente schierando nelle ultime partite, secondo probabili formazioni e cronache.
Codici ammessi: {_MANTRA_LEGEND}.
Metti i codici osservati in `ruolo_campo`. Se le fonti non parlano della sua posizione in campo, lascia `ruolo_campo` vuoto: non dedurlo dal ruolo assegnato sopra.

SE NON TROVI NULLA
Metti `confidenza` a 0, `fonti` vuoto, `ruolo_campo` vuoto e `riassunto` "Nessuna notizia rilevante nel periodo.". È la risposta corretta: non inventare un sentiment per un giocatore di cui nessuno ha scritto.
"""
