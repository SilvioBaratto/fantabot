"""The two rules-page prompts. Pure: constants in, strings out.

Both are deliberately specific about *not* inventing. The compatibility table is
published as a separate download that this repo has no copy of, so the honest
failure mode is "I could not find it" — an empty answer that fails a gate is
recoverable, a plausible fabrication is not.
"""

from __future__ import annotations

from fantabot.mantra_grid.models import ROLE_ORDER

RULES_URL = "https://www.fantacalcio.it/regolamenti/sistema-mantra"

_CODES = "Por, Dc, B, Dd, Ds, E, M, C, T, W, A, Pc"
#: Column order of every compat row. Positional, so it is stated to the
#: collector rather than assumed.
_ROLE_ORDER = ", ".join(ROLE_ORDER)

SCHEMI_PROMPT = f"""Cerca e leggi la pagina ufficiale del sistema Mantra di fantacalcio.it: {RULES_URL}

Devi estrarre i moduli tattici del sistema Mantra. Sono 11.

Per ogni modulo riporta:
- `nome`: il nome del modulo come lo scrive fantacalcio.it (es. "4-3-3").
- `slots`: i 10 slot di movimento, in ordine di linea (difesa, centrocampo, trequarti, attacco).
  Ogni slot è una lista di 1 o 2 codici ruolo intercambiabili, es. ["Dd","E"].
  Mai 3: se sulla pagina ne compaiono di più, riporta i 2 che il regolamento indica come
  alternative dello slot.

Regole da rispettare:
- Il portiere è fisso e NON è uno degli slot. Non includere Por.
- Ogni modulo schiera esattamente 5 giocatori di profilo difensivo (Dd, Ds, Dc, B, E, M)
  e 5 di profilo offensivo (C, T, W, A, Pc).
- Codici ammessi: {_CODES}.

Se non riesci a leggere la pagina o i moduli non sono 11, restituisci solo quelli che hai
letto davvero. Non completare la lista a memoria e non inventare uno slot per far tornare i
conti: una griglia incompleta viene rifiutata e rifatta, una inventata no.
"""

COMPAT_PROMPT = f"""Trascrivi la tabella di compatibilità per-modulo del sistema Mantra di
fantacalcio.it. Parti da {RULES_URL}: la pagina cita una tabella scaricabile a parte con le
compatibilità complete fra ruoli per ciascun modulo. Trova quel PDF e leggilo per intero.

Serve la tabella COMPLETA, non le eccezioni. Per ciascuno degli 11 moduli riporta:
- `schema_nome`: il nome del modulo.
- `slots`: una riga per ogni slot del modulo, il portiere per primo e poi i 10 di movimento
  nell'ordine in cui la tabella li stampa. Ogni riga ha `slot` (l'etichetta come appare, es.
  `Dc/B` o `T/A/Pc`) e `compat`: 12 valori, uno per ruolo nell'ordine {_ROLE_ORDER}.

Ogni cella è esattamente uno di:
- `ok`   schierabile senza malus;
- `-1`   schierabile con malus -1, sia in formazione sia in sostituzione;
- `-1*`  NON schierabile in fase di inserimento formazione, ammesso con malus solo nel
         calcolo finale dopo sostituzioni obbligate — nel PDF è la cella evidenziata in
         giallo, ed è la distinzione che conta di più: non confonderla con `-1`;
- `no`   mai ammesso, nemmeno con malus.

Attenzione alla legenda del PDF: dice "in colonna i ruoli previsti dallo schema, nella riga
i potenziali sostituti", ma la disposizione reale è l'opposto — le RIGHE sono gli slot del
modulo, le COLONNE i 12 codici ruolo. Fidati della disposizione, non della didascalia.

Controlli che la trascrizione deve superare: ogni slot accetta `ok` il proprio ruolo; la
riga del portiere è `ok` solo sotto Por; nessuno slot di movimento accetta un Por; nel
4-1-4-1 lo scambio W/T è `no` (in tutti gli altri moduli è `-1` o `-1*`).

In `fonti` elenca gli URL che hai letto davvero. Se non sei riuscito ad aprire il PDF,
dillo lasciando `fonti` vuoto: una tabella dichiarata incompleta si rifà, una ricostruita a
memoria dalle regole generali sembra completa e non lo è.
"""
