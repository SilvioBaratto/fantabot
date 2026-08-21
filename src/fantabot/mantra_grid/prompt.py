"""The two rules-page prompts. Pure: constants in, strings out.

Both are deliberately specific about *not* inventing. The compatibility table is
published as a separate download that this repo has no copy of, so the honest
failure mode is "I could not find it" — an empty answer that fails a gate is
recoverable, a plausible fabrication is not.
"""

from __future__ import annotations

RULES_URL = "https://www.fantacalcio.it/regolamenti/sistema-mantra"

_CODES = "Por, Dc, B, Dd, Ds, E, M, C, T, W, A, Pc"

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

COMPAT_PROMPT = f"""Cerca la tabella di compatibilità per-modulo del sistema Mantra di fantacalcio.it.
Parti da {RULES_URL}: la pagina cita una tabella scaricabile a parte con le compatibilità
complete fra ruoli per ciascun modulo. Trova quel documento e leggilo.

Per ciascuno degli 11 moduli riporta:
- `schema_nome`: il nome del modulo.
- `vietati`: le coppie [da, a] di ruoli il cui scambio è IMPOSSIBILE in quel modulo, cioè
  non ammesso nemmeno accettando il malus di -1. Lista vuota se non ce ne sono.

Riferimento noto dal regolamento: W e T sono normalmente intercambiabili con malus -1,
tranne nel modulo 4-1-4-1, dove lo scambio non è mai ammesso. Quella coppia deve comparire
fra i `vietati` del 4-1-4-1.

Non confondere i divieti generali di schieramento iniziale (B/Dd/Ds come Dc, Dd come Ds,
E come M, M come E, W come T) con i divieti per-modulo in sostituzione: qui servono questi
ultimi. Se per un modulo non trovi divieti specifici, metti `vietati` vuoto — non dedurli.

In `fonti` elenca gli URL che hai letto davvero per compilare la tabella. Se non sei
riuscito a trovare il documento con le compatibilità complete, dillo lasciando `fonti`
vuoto: una tabella dichiarata incompleta si rifà, una che si limita a restituire l'esempio
del 4-1-4-1 già citato qui sopra sembra completa e non lo è.
"""
