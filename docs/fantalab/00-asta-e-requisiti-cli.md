# 00 — Come funziona l'asta su FantaLab, e cosa serve alla CLI

Documento di partenza, in italiano e senza gergo tecnico. Due parti:

- **Parte 1** — come si svolge davvero un'asta su FantaLab, dall'inizio alla fine.
- **Parte 2** — analisi dei requisiti per uno strumento da terminale che simuli l'asta e
  aiuti a capire che strategia fare.

> **Noi giochiamo a Mantra.** Il meccanismo dell'asta (chiamata, rilancio, timer) è identico
> fra Classic e Mantra. Ma **rosa, ruoli, budget e prezzi funzionano in modo diverso**, e le
> differenze non sono dettagli: cambiano la strategia e cambiano cosa deve fare il software.
> Dove serve, il confronto col Classic è segnalato.

I dettagli tecnici (formule, campi, endpoint) stanno negli altri file della cartella. Qui si
racconta cosa succede.

---
---

# PARTE 1 — Come funziona l'asta

## 1. Che cos'è FantaLab, e cosa non è

FantaLab **non è la nostra lega**. La lega vive su `leghe.fantacalcio.it`: lì ci sono le
formazioni, i punteggi, la classifica, tutta la stagione.

FantaLab è **la stanza in cui si fa l'asta**. Serve una sera sola: si crea la stanza, si
chiamano i giocatori, si rilancia, e alla fine c'è un bottone che scarica le rose dentro
Leghe Fantacalcio. Poi FantaLab lo si può anche chiudere fino all'anno prossimo (o fino
all'asta di riparazione).

Quindi il ruolo è: **strumento per la serata dell'asta**, più un contorno di statistiche e
strumenti di preparazione che si possono usare nei giorni prima.

---

## 2. Il percorso completo, in tre momenti

### PRIMA — la preparazione

**Si crea l'asta.** Uno di noi (l'admin) apre FantaLab, sceglie **Mantra**, e imposta: quante
squadre, quanti crediti a testa, i limiti di rosa, come si chiama, come si rilancia, quanti
secondi dura il timer. Poi manda un link agli altri sette.

Per noi: **8 squadre, 500 crediti**. I limiti di rosa sono ancora da decidere — vedi §4.

**Ognuno si prepara la propria strategia.** Questa è la parte che quasi tutti saltano, ed è
quella che decide l'asta. Dentro FantaLab si può:

- dividere i giocatori in **sei fasce** (Top, Semi-Top, Terza, Quarta, Scommesse, Evitare),
  ruolo per ruolo;
- assegnare a ciascuno un **prezzo obiettivo** — quanto sei disposto a pagarlo;
- scegliere uno degli **11 schemi** Mantra e riempirlo slot per slot;
- decidere quanto budget va ai **titolari** e quanto alla **panchina** (di base 80% / 20%).

Durante l'asta vera FantaLab ricorda i tuoi prezzi e i tuoi budget, e **ti colora di rosso il
rilancio quando stai sforando**. Non ti blocca: ti avvisa.

Chi non ha voglia di farsi le fasce può **importare quelle di un creator** (ce ne sono una
quarantina, e alcune sono specifiche per Mantra), ma è roba a pagamento.

### DURANTE — la serata

Tutti entrano nella stanza. Si vede: la lista delle otto squadre con i crediti residui, il
giocatore attualmente in asta al centro, il timer, e sotto le rose di tutti in tempo reale.

Il ciclo è sempre lo stesso:

1. **Tocca a una squadra chiamare.** Il turno gira in ordine, a rotazione. La stanza scrive
   in chiaro *"È il turno di … di chiamare un giocatore"*.
2. **Il giocatore va sul banco.** Se la modalità è *chiamata libera*, lo sceglie la squadra
   di turno. Se è *random*, lo estrae il sistema.
3. **L'asta parte da 1 credito**, e chi ha chiamato è automaticamente il primo offerente.
   Cioè: se nessuno rilancia, se lo prende lui a 1.
4. **Parte il timer.** Sulla prima chiamata è più lungo (di base 20 secondi).
5. **Chiunque può rilanciare** — questa è la modalità che abbiamo scelto. I pulsanti sono
   **+1, +5, +10**, più la possibilità di digitare una cifra qualsiasi. **Ogni rilancio fa
   ripartire il timer**, di base a 10 secondi.
6. **Il timer arriva a zero** → il giocatore va all'ultimo che ha rilanciato, al prezzo
   raggiunto.
7. **Si passa al turno successivo.**

Un'unica regola contro-intuitiva ma importante: **non puoi rilanciare su te stesso**. Se sei
già tu il migliore offerente, i pulsanti sono spenti.

In alcune leghe l'admin deve **confermare ogni acquisto** prima che si proceda (la stanza
scrive *"Aspetta che l'admin confermi l'acquisto"*). È un'impostazione, da decidere.

### DOPO — la chiusura

Quando le rose sono complete, l'admin segna l'asta come conclusa. FantaLab genera un
riepilogo, una classifica delle rose e (dopo qualche ora) un video con i momenti salienti.

Poi c'è **Esporta in Fantaleghe**, che riversa le rose dentro la nostra lega su
`leghe.fantacalcio.it`.

---

## 3. La regola che quasi nessuno conosce: il "MAX"

Accanto ai crediti di ogni squadra c'è un numero marcato **MAX**. È **il massimo che puoi
offrire adesso**, e non è un consiglio: FantaLab non ti fa proprio cliccare il rilancio se
sfori.

Il motivo è che **devi poter completare la rosa minima**. Se ti mancano ancora 26 giocatori
obbligatori e ognuno costa almeno 1 credito, devi tenerne da parte 25 per gli altri.

Su una lega Mantra vera osservata dal vivo (8 squadre, 500 crediti, minimo 26 giocatori) il
MAX iniziale era **475**, non 500.

### E qui c'è la parte importante, che nel Classic non esiste

In Mantra il **minimo** e il **massimo** di rosa sono numeri diversi: per esempio minimo 26,
massimo 31. Il MAX mette da parte i crediti solo per il **minimo**.

Quindi: **quando hai raggiunto il minimo, il MAX sparisce.** Da quel momento puoi mettere
tutti i crediti che ti restano su un singolo giocatore.

Nel Classic questo non succede mai, perché minimo e massimo coincidono (25 e 25). In Mantra è
un'arma vera per il finale d'asta, ed è la cosa che più facilmente ci si dimentica di sfruttare
— e di temere, quando la usa un avversario.

---

## 4. I numeri della nostra lega

Ipotizzando minimo 26 / massimo 31 (**da confermare**, vedi §7):

- In totale girano **4.000 crediti** (8 × 500).
- I giocatori che la lega è **obbligata** a comprare sono **208** (8 × 26). Ne può comprare
  fino a 248.
- Fa una media di circa **19 crediti a giocatore**.
- Il listone ha circa **519 giocatori**. Più della metà rimane invenduta, quindi **c'è sempre
  un giocatore a 1 credito**: nessuno resta con la rosa incompleta.
- Da un'asta Mantra vera identica alla nostra: **Malen 192** e **Martinez L. 191**, cioè quasi
  il 40% del budget su un solo attaccante, e due volte nella stessa serata. Sotto, Yildiz 51 e
  Maignan 41.

Da cui la cosa più importante: **l'asta si decide su quattro o cinque giocatori, non su
trenta.** Tutto il resto è riempimento.

---

## 5. I ruoli Mantra: la differenza vera

Nel Classic ci sono quattro ruoli: P, D, C, A. In Mantra ce ne sono **dodici**:

```
Por · Dc · B · Ds · Dd · E · M · C · W · T · A · Pc
```

che si raggruppano in **cinque reparti** (non quattro): Porta, Difesa, Centrocampo,
**Trequarti**, Attacco.

Tre conseguenze pratiche, tutte importanti:

**1. Un giocatore può avere più ruoli.** Nei nostri dati **il 44% del listone è multi-ruolo**:
`M;C`, `C;T`, `DS;E`, `W;A` e così via. Un giocatore con due ruoli entra in più caselle di più
schemi — vale di più, e questo valore non esiste nel Classic.

**2. Non ci sono quote per ruolo.** Questa è la differenza che sorprende di più: in Mantra
FantaLab **non ti obbliga a comprare un difensore**. Le uniche due opzioni di limite sono
"nessun limite" (solo un minimo e un massimo di rosa) oppure "min/max portieri e min/max
movimento". Nient'altro.

Vuol dire che **puoi finire l'asta con 3 portieri e 23 attaccanti e la stanza non dirà
niente.** Poi però non riesci a schierare una formazione legale. Il vincolo esiste, ma scatta
dopo, quando fai la formazione — non durante l'asta.

**3. La rosa si pensa per schema, non per reparto.** FantaLab ha **11 schemi**:

```
3-4-3 · 3-4-1-2 · 3-4-2-1 · 3-5-2 · 3-5-1-1
4-3-3 · 4-3-1-2 · 4-4-2 · 4-1-4-1 · 4-4-1-1 · 4-2-3-1
```

Ognuno è fatto di 11 caselle tipizzate, e alcune accettano due ruoli. Un 4-4-2 è:

```
Por · Ds · Dc · Dc · Dd · W/E · C · C/M · E · Pc/A · Pc/A
```

Per questo il pianificatore Mantra non divide il budget per reparto (come fa il Classic), ma
fra **titolari e panchina**, 80% / 20% di base.

---

## 6. Cosa si vede nella stanza

Otto schede in basso. In breve:

| Scheda | A cosa serve |
|---|---|
| **Rose Squadre** | Le otto rose in tempo reale: crediti, MAX, slot mancanti (in Mantra divisi in `P Min` e `Mov Min`), e quanto ha pagato ciascuno ogni giocatore. |
| **Fasce Giocatori** | Tutto il listone come tabella di lavoro, raggruppato per fascia e ruolo. |
| **Recap Asta** | Il tuo cruscotto: crediti, rilancio massimo, giocatori comprati, e **quanti giocatori di ogni fascia restano liberi — per ognuno dei 12 ruoli Mantra**. |
| **Guida all'Asta** | Le probabili formazioni squadra per squadra: titolari, ballottaggi con le percentuali, rigoristi. |
| **Portieri** | Gli abbinamenti fra portieri di club diversi, con un punteggio su 100 sul calendario. |
| **Avversari** | Le formazioni migliori degli altri sette, una accanto all'altra. |
| **Simula Rosa/Budget** | Il pianificatore per schema, disponibile anche a asta in corso. |
| **Analisi Asta** | Chi ha pagato sopra e sotto il mercato: i più costosi, gli affari, gli strapagati. |

Nota sul Recap: la scarsità in Mantra si legge su **12 ruoli**, non su 4. "Restano due `Dc`
Top" e "restano due `Pc` Top" sono due informazioni diverse, ed entrambe pesano più che nel
Classic proprio perché nessuna quota obbliga nessuno a comprarli.

Buona parte dei numeri predittivi (prezzo medio pagato nelle altre aste, fantamedia attesa) è
**a pagamento**. Sono esattamente le cose che ci possiamo calcolare da soli.

---

## 7. Le impostazioni ancora da decidere

Quattro interruttori. Due li abbiamo già fissati.

**Rilancio** — ✅ **libero**: chiunque rilancia contro il cronometro.

**Modificatore di difesa** (in Mantra si chiama **D. Factor**) — ✅ **no**.

**Limiti di rosa** — ⏳ *da decidere, ed è la domanda più importante che resta.* In Mantra ci
sono solo due modi:
- *nessun limite per ruolo*: un minimo e un massimo di giocatori totali (di base 25/30);
- *min/max portieri e min/max movimento*: es. portieri 3, movimento 23-28.

I due numeri (minimo e massimo) decidono il MAX e decidono se esiste lo sblocco di §3.

**Modalità di chiamata** — ⏳ *da decidere*, si va verso **random**. È anche l'uso comune:
delle dieci aste Mantra pubbliche in corso durante la ricognizione, **sette erano random** e
tre a chiamata.

E una che non abbiamo mai chiesto: **Imbattibilità Portiere** sì o no. Se è attiva, i portieri
valgono sensibilmente di più.

C'è anche l'opzione *"chiama al prezzo di quotazione"*: invece di partire da 1, si parte dal
prezzo di listino. Se venisse attivata sparirebbe tutta la coda dei giocatori a 1-3 crediti.

> **Nessuna di queste incertezze blocca il software.** Sono tutte impostazioni che si
> rispondono all'avvio, e sulle quali la simulazione può girare a tappeto finché non ci sono
> risposte definitive — vedi §13.

---

## 8. Se si sbaglia

Due reti di sicurezza, entrambe in mano all'admin:

- **Chiamata annullata**: si butta via un'asta in corso. Il giocatore torna disponibile,
  nessuno paga niente. Il turno però va avanti lo stesso.
- **Acquisto eliminato**: si cancella un acquisto già concluso, e *"la squadra che lo possiede
  riprenderà tutti i crediti"*. Rimborso pieno.

Gli errori si correggono.

---
---

# PARTE 2 — Requisiti per la soluzione da terminale

## 9. Il problema da risolvere

Un'asta si gioca in due momenti completamente diversi, e servono due cose diverse.

**Prima dell'asta** non sai se la tua strategia regge. L'unico modo per saperlo è **giocare
l'asta molte volte** contro avversari plausibili e guardare come finisce.

**Durante l'asta** hai **dieci secondi**. Esce un `C;T` a 34, il timer scende, e devi decidere
se 35 è un affare o un errore. Non c'è tempo per aprire un foglio di calcolo.

Quindi lo strumento ha **due modalità d'uso**:

| | Modalità **Palestra** | Modalità **Copilota** |
|---|---|---|
| Quando | Nei giorni prima | Durante l'asta, in parallelo alla stanza FantaLab |
| Cosa fa | Gioca centinaia di aste finte e riporta le statistiche | Segue l'asta vera e dice, giocatore per giocatore, il prezzo di rinuncia |
| Vincolo dominante | Nessuno | **Risposta sotto i 2 secondi, leggibile in un'occhiata** |
| Output | Distribuzioni, confronti fra strategie | Un numero: *fin dove arrivo* |

Stesso motore, due interfacce.

---

## 10. Il requisito che in Mantra viene prima di tutti

**Il software deve sapere se una rosa è schierabile.**

Nel Classic questo problema non esiste: compri 3-8-8-6 perché il sistema ti ci obbliga, e una
formazione legale c'è sempre. In Mantra nessuno ti obbliga a niente durante l'asta, e la
legalità si scopre solo dopo.

Serve quindi una funzione che, data una rosa, risponda: **quanti degli 11 schemi riesco a
schierare?** È un problema di accoppiamento fra i ruoli dei giocatori e le caselle dello
schema — con la complicazione che i giocatori possono avere più ruoli e le caselle possono
accettarne due.

Da lì discende tutto il resto:

- una rosa non si valuta solo per quanto è costata, ma per **quanti schemi tiene aperti**;
- un giocatore multi-ruolo vale un premio, perché apre caselle;
- durante l'asta la domanda giusta non è "mi manca un difensore?" ma **"se salto questo
  giocatore, quanti schemi perdo?"**.

Questa funzione è **il pezzo di lavoro nuovo più grosso del progetto**. I dati per farla ci
sono già in casa (`data/mantra_schemi.json`, e i ruoli `;`-separati in
`data/quotazioni_mantra.csv`).

---

## 11. Requisiti funzionali — modalità Palestra

Priorità: **[M]** indispensabile, **[S]** importante, **[C]** se avanza tempo.

**[M] Riprodurre fedelmente le regole.** Turno a rotazione, partenza da 1, chi chiama è il
primo offerente, timer che riparte a ogni rilancio, divieto di rilanciare su se stessi, e
soprattutto **il MAX calcolato sul minimo di rosa, con lo sblocco quando il minimo è
raggiunto**. Sbagliare il MAX produce rose che nella realtà non potrebbero esistere.

**[M] Legalità della rosa** (§10). Una simulazione che produce 23 attaccanti non ha detto
niente di utile.

**[M] Avversari credibili.** Sette bot con crediti, slot e preferenze, **diversi fra loro**:
uno che si svena su una punta, uno che compra tanti multi-ruolo, uno che non paga mai sopra il
listino. Nell'asta Mantra osservata due squadre hanno messo ~190 su un solo `Pc`: se nessun bot
lo fa mai, il simulatore sottostima il costo dei top.

**[M] Eseguire N aste e riportare aggregati.** "Su 500 aste con questa strategia, quanto mi è
costato in media il secondo `Pc`, e quante volte l'ho perso."

**[M] Confrontare strategie.** Due impostazioni diverse, quale produce la rosa migliore più
spesso.

**[S] Riproducibilità.** Stesso seed, stessa asta.

**[S] Report leggibile.** Prezzo mediano per ruolo e per fascia, crediti non spesi, quali
giocatori sono sempre contesi, quanti schemi resta capace di schierare la rosa finale.

**[C] Ottimizzatore.** Cercare da solo la strategia migliore. Viene dopo: prima serve un
simulatore di cui fidarsi.

---

## 12. Requisiti funzionali — modalità Copilota

**[M] Il prezzo di rinuncia.** La funzione centrale. Dato un giocatore e lo stato dell'asta,
un numero solo: **fin dove arrivo**.

**[M] Ricalcolo dinamico.** Quel numero si muove durante la serata: se metà dei crediti della
lega è già bruciata e restano 150 giocatori da assegnare, i prezzi crollano. *(Su FantaLab
questa funzione esiste, si chiama "Prezzo reattivo", ed è a pagamento.)*

**[M] Scarsità sui 12 ruoli.** "Restano due `Dc` Top" deve alzare il prezzo di quei due.

**[M] Impatto sugli schemi.** Prendere o non prendere questo giocatore quanti schemi mi apre o
mi chiude. È la versione Mantra del "mi manca un difensore".

**[M] Inserimento rapido di quello che succede.** Ogni assegnazione va registrata in pochi
secondi: *chi, quanto, a chi*. Se registrare costa più di tre secondi, a metà serata smetterò
di farlo e lo strumento diventa inutile. **Questo è il vero rischio di adozione**, non la
qualità del modello.

**[S] Vincoli sempre visibili.** Quanti giocatori mi mancano al minimo, il MAX corrente, e
**se il MAX si è già sbloccato**.

**[S] Allarme sforamento** sul budget titolari/panchina che mi ero dato.

**[C] Suggerimento di chiamata.** Ha senso solo se la lega sceglie *chiamata libera*. In
modalità random va nascosto.

---

## 13. Configurazione a runtime — deve funzionare su qualsiasi asta Mantra

**Principio.** Lo strumento non è scritto per la nostra lega. È scritto per **una qualsiasi
asta Mantra su FantaLab**, e la nostra lega è soltanto un profilo salvato.

Due motivi pratici, entrambi concreti:

- metà delle nostre impostazioni non è decisa, e alcune si decideranno la sera stessa;
- di aste ce ne saranno altre — la riparazione a gennaio, la prossima stagione, la lega di un
  amico che ci chiede una mano. Riscrivere il software ogni volta non ha senso.

Ne segue la regola dura: **se un numero o una regola d'asta compare scritto nel codice, è un
bug.** Tutto quello che una lega può cambiare deve essere un parametro.

### Tutto quello che deve essere parametro

Questa è la lista completa di ciò che FantaLab lascia decidere a una lega Mantra, più le
nostre incognite. Il default indicato è quello di FantaLab, da usare finché nessuno dice
diversamente.

**La lega**

| Cosa | Valori | Default |
|---|---|---|
| Numero squadre | 2–12+ (viste dal vivo: 4, 8, 10, 11, 12) | 8 |
| Crediti a squadra | qualsiasi (viste: 500, 660, 700, 1000) | 500 |
| Crediti per singola squadra | **override per squadra** — i budget possono essere asimmetrici | uguali |
| Listone | Serie A / Euroleghe | Serie A |
| Stagione | anno del listone | corrente |

**La rosa**

| Cosa | Valori | Default |
|---|---|---|
| Tipo di limite | *nessun limite per ruolo* / *min-max portieri e movimento* | nessun limite |
| Minimo e massimo rosa | interi | 25 / 30 |
| Min-max portieri | interi | 2 / 5 |
| Min-max movimento | interi | 20 / 30 |
| Quante squadre possono avere lo stesso giocatore | 1 o più | 1 |

**La meccanica d'asta**

| Cosa | Valori | Default |
|---|---|---|
| Modalità di chiamata | chiamata libera / random / alfabetico / draft / draft con sbusto | chiamata libera |
| Modalità di rilancio | libera / in ordine (poker) | libera |
| Si parte da 1 o dalla quotazione | sì / no | da 1 |
| Timer prima chiamata | secondi | 20 |
| Timer ogni rilancio | secondi | 10 |
| Passi di rilancio disponibili | lista (es. 1, 5, 10) + importo libero | 1, 5, 10 |
| Asta Ninja (informazioni nascoste) | sì / no | no |
| L'admin conferma ogni acquisto | sì / no | no |
| Squadre escluse dai turni | lista, modificabile in corsa | vuota |

**Le regole di punteggio** — non toccano l'asta, ma cambiano quanto vale un giocatore

| Cosa | Valori | Default |
|---|---|---|
| D. Factor (modificatore difesa) | sì / no | no |
| Imbattibilità portiere | sì / no | da chiedere |
| Schemi ammessi | sottoinsieme degli 11 | tutti e 11 |
| Fuori ruolo | vietato / ammesso con malus / libero | come `mantra_compat.json` |

**I dati**

| Cosa | Valori | Default |
|---|---|---|
| Prezzo di riferimento | quotazione / valore di mercato / prezzi nostri / prezzo medio aste | prezzi nostri |
| Fattore di riscalatura dei prezzi | numero, oppure **calcolato** dai crediti in lega | calcolato |
| Numero e nomi delle fasce | 5 o 6, nomi liberi (una lega può rinominarle) | 6, nomi FantaLab |
| Cambio ruolo | l'admin può riassegnare il ruolo di un giocatore **solo per quell'asta** | nessuno |
| File degli schemi e delle compatibilità | percorso | quelli in `data/` |

**La nostra strategia** — non è una regola della lega, ma va parametrizzata lo stesso

| Cosa | Default |
|---|---|
| Divisione budget titolari / panchina | 80 / 20 |
| Profilo di rischio | equilibrato |
| Fasce e prezzi obiettivo | i nostri |

### Come si riempie la configurazione, in ordine

**1. Leggendola dall'asta stessa.** *[S], ed è la cosa che più fa la differenza.* Si incolla il
link della stanza FantaLab e si prova a dedurre tutto: la stanza mostra già crediti, numero
squadre, D. Factor, Imbattibilità Portiere, i minimi `P` e `Mov`, la modalità di chiamata e i
crediti di ognuno. **Meglio non chiedere niente che chiedere bene.**

**2. Da un profilo salvato.** Uno per lega. La nostra Mantra e la nostra Classic non si
mescolano mai.

**3. Chiedendo.** Solo quello che dopo i primi due passi manca ancora.

**4. Default FantaLab.** Per tutto il resto.

Sopra a tutto stanno i flag da riga di comando, così le simulazioni in batch non fanno mai
domande.

### Impararla guardando

*[S] — questo è ciò che rende lo strumento davvero adattabile.* Diverse impostazioni si
deducono dall'asta in corso senza che nessuno le dichiari:

- **la durata del timer**, cronometrando due rilanci consecutivi;
- **se si parte da 1 o dalla quotazione**, guardando il prezzo di apertura della prima
  chiamata;
- **quanti giocatori sono obbligatori**, invertendo la formula del MAX: se una squadra ha
  `C` crediti e un MAX di `M`, allora le mancano `C − M + 1` acquisti obbligatori;
- **se il tetto si è già sbloccato** per qualcuno: il suo MAX è uguale ai suoi crediti;
- **l'ordine di rotazione delle chiamate**, osservando qualche giro.

E soprattutto: il copilota deve **controllare di continuo che la configurazione dichiarata
coincida con quello che vede**, e dirlo quando divergono — *"hai impostato il timer a 10
secondi, ne sto misurando 15"*. Un'impostazione sbagliata all'inizio della serata avvelena
ogni numero fino alla fine, e senza questo controllo non te ne accorgi mai.

### Quando qualcosa resta sconosciuto

Non si blocca e non si inventa. Si mostra **un intervallo invece di un numero**, e si dice
quale risposta lo stringerebbe di più:

```
  prezzo di rinuncia: 34–41   (incerto: minimo rosa non noto)
  → saperlo stringerebbe l'intervallo a ±2
```

Così l'incertezza resta visibile a chi decide, invece di nascondersi dentro una cifra falsa.

### La spazzata di sensibilità

Le incognite non sono un blocco: sono una domanda a cui la Palestra risponde da sola.
Invece di aspettare i numeri veri, si fa girare la simulazione **su tutto l'intervallo
plausibile** e si guarda quanto cambia il risultato.

```
  min rosa      25    26    27    28    30
  spesa 1° Pc  188   191   190   186   179
  schemi ok    7.2   7.1   7.0   6.8   6.4
```

Dove il risultato si muove poco, la domanda non era importante. Dove si muove tanto, sappiamo
**quale chiedere per prima** al gruppo, e possiamo dirlo con un numero invece che a
sensazione.

Giovedì smette così di essere un prerequisito e diventa quello che dovrebbe essere: un modo
per **restringere un intervallo** su qualcosa che gira già.

### Coerenza: cosa va rifiutato

Configurazioni aritmeticamente valide ma impossibili vanno bloccate subito, con un messaggio
che dice *cosa* non torna:

- minimo maggiore del massimo, in qualsiasi banda;
- `min portieri + min movimento` diverso dal minimo totale;
- una rosa minima che **non può schierare nessuno degli 11 schemi** (esempio: 1 portiere e 10
  di movimento — i conti tornano, il calcio no);
- minimo × numero squadre più grande del listone;
- timer a zero, o timer di rilancio più lungo di quello di prima chiamata.

E un avviso non bloccante quando *"parti dalla quotazione"* è attivo: cancella tutta la coda
dei giocatori a 1-3 crediti, e ogni modello di prezzo a valle deve saperlo.

### Cosa invece NON si configura

Le regole del **motore** FantaLab, che nessuna lega può cambiare: non puoi rilanciare su te
stesso; il MAX riserva sul minimo di rosa e si sblocca quando lo raggiungi; chi chiama è il
primo offerente; ogni rilancio fa ripartire il timer. Quelle sono la piattaforma, non la lega,
e vanno scritte una volta sola.

---

## 14. Requisiti sui dati

| Dato | Serve per | Dove lo prendiamo |
|---|---|---|
| Ruoli Mantra (`;`-separati) | Legalità, scarsità, premio multi-ruolo | `data/quotazioni_mantra.csv` ✔ |
| Gli 11 schemi | Legalità | `data/mantra_schemi.json` ✔ |
| Quotazione e valore di mercato Mantra | Prezzo di riferimento | già in casa ✔ |
| Titolarità / affidabilità / integrità | Distinguere titolare da riserva | FantaLab, in parte già nostro |
| Fasce | Scarsità, e la nostra strategia | da definire noi |
| Prezzi realmente pagati in aste Mantra vere | **Calibrare tutto** | `Osserva Aste` — 45 aste Mantra pubbliche in corso |
| Sentiment e infortuni | Correggere la titolarità | già nostro (`news-fetch`) |

**Una nota di calibrazione che vale da sola metà del lavoro.** I nostri prezzi obiettivo
Mantra (`target_price_2026_27_mantra.csv`) hanno mediana **5** e massimo **34**: sono sulla
scala delle quotazioni, non sui crediti della nostra lega. La somma dei primi 208 fa **2167**,
ma in lega girano **4.000 crediti**. Vanno quindi **moltiplicati per circa 1,7** prima di
usarli come tetto di offerta. Usati così com'è ci farebbero rinunciare a quasi ogni giocatore
conteso.

---

## 15. Requisiti non funzionali

**[M] Risposta sotto i 2 secondi.** Il vincolo è il timer da 10 secondi.

**[M] Il numero non dipende dalla rete.** *(Corretto il 2026-08-26. Qui c'era scritto
"funziona senza rete: durante l'asta niente chiamate a internet". Era sbagliato: durante l'asta
siamo connessi, e lo strumento la rete la usa — per il giudizio LLM in tempo reale, vedi
`tasks/asta-plan.md`.)*

Quello che resta vero è il motivo per cui la regola era stata scritta. **Il prezzo di rinuncia
si calcola in locale e compare comunque**, anche se la rete cade, anche se una chiamata va in
timeout. La rete è uno strato sopra, non il percorso critico: nessuna schermata aspetta un
socket, e tutto quello che serve al numero è scaricato prima.

**[M] Leggibile a colpo d'occhio.** Una schermata sola, i numeri che contano grandi.

**[S] Nessuna automazione sulla stanza FantaLab.** Lo strumento **consiglia**, non clicca. È
una scelta, non un limite tecnico.

**[S] Recupero da crash.** Se il terminale muore a metà asta, riaprendolo lo stato è quello di
prima.

---

## 16. Cosa NON deve fare

- Non deve **partecipare** all'asta al posto nostro.
- Non deve **replicare l'interfaccia** di FantaLab. La stanza vera resta aperta accanto.
- Non deve **prevedere la stagione**. Serve a comprare bene.
- Non deve dipendere da **abbonamenti a pagamento**.

---

## 17. Come si vede se funziona

**Fedeltà delle regole.** Lista di controllo automatica: il turno gira, si parte da 1, il timer
riparte, il MAX riserva sul minimo, il MAX si sblocca quando il minimo è raggiunto, una rosa
che non può schierare nessuno degli 11 schemi viene segnalata come rotta.

**Realismo dei prezzi.** Confrontare i prezzi simulati con quelli di un'asta Mantra vera già
osservata (8 squadre, 500 crediti). Se il primo `Pc` va via a 60 mentre nella realtà è andato a
192, il modello degli avversari è troppo timido.

**Prova sul campo.** La prova d'asta di giovedì con Luca: tenere aperta la CLI accanto alla
stanza e scrivere il prezzo di rinuncia *prima* che si chiuda. È l'unico test che conta.

---

## 18. Rischi

| Rischio | Perché fa male | Cosa lo riduce |
|---|---|---|
| **Non riesco a stare dietro all'inserimento dati** | Lo strumento si disallinea a metà asta e va buttato | Inserimento in pochi tasti, e ripartenza da uno stato inserito a mano |
| **Prezzi non riscalati** | Rinuncio a tutti i giocatori contesi credendo di essere disciplinato | Il fattore ~1,7 di §14, ricalcolato sui limiti veri |
| **Ignoro la legalità della rosa** | Simulazioni che producono rose non schierabili: numeri inutili | La funzione di §10, come requisito bloccante |
| **Bot troppo prudenti** | Sottostimo il costo dei top e il piano salta al terzo giocatore | Calibrare sui prezzi veri di `Osserva Aste` |
| **Dimentico lo sblocco del MAX** | Un avversario a minimo raggiunto mi passa sopra con tutto il budget | Modellarlo, e tenerlo in schermata |

---

## 19. In sintesi

L'asta Mantra è un problema di **allocazione sotto vincolo**, come il Classic, ma i vincoli
sono di natura diversa: non ci sono quote per ruolo durante l'asta, e la vera restrizione —
riuscire a schierare una formazione legale — **non viene applicata dalla piattaforma**. Se la
applichiamo noi, è un vantaggio; se ce la dimentichiamo, è un disastro a fine serata.

Dentro quei vincoli la partita si gioca su **quattro o cinque giocatori contesi**. Tutto il
resto costa fra 1 e 10 crediti.

Quindi lo strumento deve saper fare bene due cose: **dire fin dove arrivare su un giocatore**,
e **sapere quali formazioni quella scelta lascia aperte**. La modalità Palestra serve a tarare
quei numeri prima; la modalità Copilota serve a leggerli in tempo.
