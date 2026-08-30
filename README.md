# fantabot

100% autonomous fantacalcio manager for `leghe.fantacalcio.it`: weekly lineup
submission, asta iniziale (initial auction), asta di riparazione (repair
auction) — all handled without a human clicking anything, once the site's DOM
is mapped and a stats source is wired in.

## News sentiment (`fantabot news fetch`)

One Claude Agent SDK query per player over `WebSearch` + `WebFetch`, validated
against a pydantic schema, appended weekly to `data/player_sentiment_2026-27.csv`
as a per-player time-series. Runs on the Claude Code OAuth subscription — no
`ANTHROPIC_API_KEY` anywhere.

```bash
fantabot news fetch --limit 5      # smoke test: queries, writes nothing
fantabot news fetch --write        # the weekly run, all 523 quotati
fantabot news fetch --write --force --lookback-days 21
```

Each row carries an overall `sentiment` plus `disponibilita`, `titolarita`,
`mercato`, `forma`, `rigorista`, `piazzati` and a `confidenza`, with an Italian
`riassunto` and the URLs actually read. `confidenza = 0` means *no coverage was
found* — not a neutral player — and readers must exclude those rows from averages.

It also collects the one Mantra statistic no file in `data/` can hold. fantacalcio.it
assigns Mantra roles in late July and never revisits them, so `quotazioni_mantra.csv`
drifts from reality by design; `ruolo_campo` records what a player is *actually*
being played as, and `deriva_ruolo` flags when the frozen tag has gone stale.

Suggested cron (Wednesday mornings, in-season):

```cron
0 9 * * 3 cd /path/to/fantabot && /path/to/conda-env/bin/fantabot news fetch --write >> data/news_cron.log 2>&1
```

## Mantra tactical grid (`fantabot mantra-grid`)

One-off, **not** on cron. Collects the 11 Mantra schemas and the per-formation
out-of-position matrix into `data/mantra_schemi.json` and `data/mantra_compat.json`,
behind six fail-closed gates. These are the input to a Mantra lineup engine, which
does not exist yet — `models.Role` and `VALID_FORMATIONS` are Classic-only.

```bash
fantabot mantra-grid          # collect and gate, write nothing
fantabot mantra-grid --write  # write only if every gate passes
```

## Status

The asta path is live-capable: `asta-optimize` plans, `asta-live` advises off a
real room, and `asta-bid` places bids over FantaLab's unauthenticated RTDB —
gated behind `FANTABOT_AUTO_ACT`, which is `false` by default.

**Weekly lineup submission is not built.** The Classic scaffolding that stood in
for it — `lineup.py`, `auction.py`, `strategy.py` and their DOM stubs — was
removed rather than left raising `NotImplementedError` against a DOM nobody
mapped. When it is wanted it gets built on the `apileague.fantacalcio.it` JSON
endpoints documented in `docs/leghe-api.md`, not on scraped markup.

Persistence is done: all ten scraped CSVs, the sentiment series, the lineup
guard and the auction budget live in Postgres, and the auction budget now
survives a mid-asta restart.

## Setup

```bash
conda activate fanta
pip install -e ".[dev]"   # re-run this whenever pyproject.toml changes
playwright install chromium
cp .env.example .env   # fill in LEGA_EMAIL / LEGA_PASSWORD / LEGA_URL / FANTABOT_LEAGUE_ID

# generate an encryption key and paste it into .env as FANTABOT_ENCRYPTION_KEY.
# It encrypts the bearer token at rest; without it `fantabot auth login` refuses to
# open a browser. Never commit it, and never pass it on the command line.
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d    # Postgres on 54321, Adminer on http://localhost:18082
alembic upgrade head

# Fill an empty database from the site. This is the only way that works on a
# fresh clone: data/'s CSVs are git-ignored (.gitignore:8), so they have never
# been part of a checkout. Measured end to end on 2026-08-26 — under 5 minutes
# for four seasons, most of it the voti leg.
fantabot db scrape quotazioni    # ~1 GET/season  -> players, teams, quotazioni
fantabot db scrape statistiche   # ~3 GETs/season -> statistiche
fantabot db scrape voti          # ~38 GETs/season, 1s apart -> voti, bonus_malus
fantabot db price --system classic   # NOTE: one system per run,
fantabot db price --system mantra    # --system defaults to classic
fantabot db dump                 # restore point, outside the repo

fantabot db check         # health, per-table row counts and sizes

fantabot auth login          # interactive, opens a real browser — log in once
fantabot auth status   # what is stored, when it expires, whether it still works
fantabot config-check   # sanity check env is loaded (secrets masked)
pytest                  # decision logic; opens zero sockets
pytest -m db            # integration tier, needs the stack above
```

## Storage

**The `apileague` bearer token lives in Postgres, encrypted.** `fantabot auth login`
opens a real browser, you sign in yourself, and it reads each lega's token out
of `localStorage`, encrypts it with `FANTABOT_ENCRYPTION_KEY` and writes it to
`league_tokens` keyed by lega. Nothing reads a token from disk.

`data/storage_state.json` is **opt-in** now (`fantabot auth login --save-session`).
It holds Playwright's cookies, and as of 2026-08-26 no working code path reads
them — so the default run does not create it.

What the key protects: a database dump, a shared Postgres, Adminer's web UI. It
sits in `.env` beside the database password, so it does not protect against
someone who can read `.env`. That is the honest boundary, and it is still
strictly better than a plaintext token in a file that gets rsynced and backed up.

Postgres is the source of truth. The CSVs in `data/` are the one-time seed it
was built from and nothing reads them any more — the scrapers, the analysis
scripts and `news-fetch` all go through the database. See
[`data/README.md`](data/README.md) for the table dictionary, and
[`docs/spec-postgres-persistence.md`](docs/spec-postgres-persistence.md) for why
each departure from the file layout was made.

```bash
docker compose up -d              # db + adminer, nothing else
alembic upgrade head              # apply migrations
alembic check                     # do models and migrations still agree?
```

## Commands

```bash
fantabot auth login           # interactive login; stores each lega's token encrypted
fantabot auth status    # stored / expires / state, per lega — works with no key
fantabot auth forget    # remove one lega's row; --league required, no --all
fantabot config-check    # print resolved settings, secrets masked
fantabot db check        # database health + per-table row counts and sizes
fantabot news fetch      # weekly sentiment run; --write stores it
fantabot mantra-grid     # one-off, collects the Mantra schema grid
```

## Safety

`FANTABOT_AUTO_ACT=false` by default (`.env.example`) — every action logs what
it *would* do without clicking anything real. Flip to `true` only after
verifying selectors against the live site in a low-stakes matchday.

## Scheduling

- **Auctions (iniziale / riparazione)**: live sessions, not a point in time.
  `fantabot asta bid` is a long-lived polling loop, started shortly before the
  scheduled auction and left running for its duration — not fired once from cron.

Example crontab. cron gets no shell profile, so the conda env's binary is named
in full rather than relying on an activated environment:
