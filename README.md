# fantabot

100% autonomous fantacalcio manager for `leghe.fantacalcio.it`: weekly lineup
submission, asta iniziale (initial auction), asta di riparazione (repair
auction) — all handled without a human clicking anything, once the site's DOM
is mapped and a stats source is wired in.

## Install & run

The easiest way to run the project is the local web app in [`app/`](app/) — one
command, no Docker. The only tools you install by hand are **git** and
[`uv`](https://docs.astral.sh/uv/); `uv` brings its own Python, Postgres, and compiled
frontend. Nothing is published, so you install from a clone of this repo:

```bash
git clone https://github.com/SilvioBaratto/fantabot.git
cd fantabot

uv tool install ./app     # installs the `fantabot-app` command (fantabot resolved as a path dep)
fantabot-app setup        # provisions its own Postgres (bundled PG18), migrates, installs chromium
fantabot-app              # serves the UI at http://127.0.0.1:8000 and opens your browser
```

That's the whole install. `fantabot-app setup` is safe to re-run; `fantabot-app` is the
everyday launch; `fantabot-app doctor` diagnoses a broken setup.

> **Fresh clone?** The compiled UI (`fantabot_app/web`) is a git-ignored build artifact.
> Released builds bundle it; from a raw checkout, build it once first (needs Node + npm):
> `python app/scripts/build_frontend.py`. Until you do, the app serves a placeholder page.

Full app documentation — everyday commands, where data lives, developer setup — is in
[`app/README.md`](app/README.md). The rest of this file documents the underlying
`fantabot` **CLI** (the engine the app drives) and its developer setup.

## News sentiment (`fantabot news fetch`)

One Claude Agent SDK query per player over `WebSearch` + `WebFetch`, validated
against a pydantic schema, written weekly to `player_sentiment` in Postgres
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
out-of-position matrix into `src/fantabot/data/mantra_schemi.json` and
`mantra_compat.json`, behind six fail-closed gates. They ship as package data because
they are the legality matcher's input, not runtime state — `domain/asta/legality.py`
reads them through `importlib.resources`, so `asta legality` works from any directory.

```bash
fantabot mantra-grid          # collect and gate, write nothing
fantabot mantra-grid --write  # write only if every gate passes
```

## Status

The asta path is live-capable: `asta optimize` plans, `asta live` advises off a
real room, and `asta bid` places bids over FantaLab's unauthenticated RTDB —
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
scripts and `news fetch` all go through the database. See
[`data/README.md`](data/README.md) for the table dictionary, and
`docs/spec-postgres-persistence.md` for why
each departure from the file layout was made.

```bash
docker compose up -d              # db + adminer, nothing else
alembic upgrade head              # apply migrations
alembic check                     # do models and migrations still agree?
```

## Commands

One CLI, five groups and two one-offs. `fantabot --help` is the whole surface.

```bash
fantabot asta optimize --lam 0.3 --budget 500   # the roster to aim for
fantabot asta legality --rosa "1,2,3"           # which of the 11 schemi this rosa fields
fantabot asta live --league <id> --db <shard> --team <id>    # advise off a live room
fantabot asta bid  --league <id> --db <shard> --team <id> --user <id>

fantabot harvest scan --seed seed.json                       # which auctions are live
fantabot harvest collect --seed seed.json --out landing.jsonl --pool 800
fantabot harvest load landing.jsonl --seed seed.json --follow
fantabot harvest backfill events.jsonl --seed seed.json

fantabot db check                    # health, per-table row counts and sizes
fantabot db scrape quotazioni        # also statistiche, voti
fantabot db price --system mantra --top-n 15

fantabot auth login                  # interactive; stores each lega's token encrypted
fantabot auth status                 # stored / expires / state, per lega — works with no key
fantabot auth forget --league <id>   # one row at a time, no --all
fantabot auth fantalab-login         # headed, manual; session encrypted into Postgres

fantabot news fetch --write          # the weekly sentiment run

fantabot config-check                # resolved settings, secrets masked
fantabot mantra-grid --write         # one-off, collects the Mantra schema grid
```

## Layout

Four layers, dependencies pointing inward, enforced over every module by
`tests/test_layers.py`:

```
src/fantabot/
  domain/        pure decisions — asta, harvest, news, mantra, tokens, shared
  application/   use cases — asta_planner, harvest_loader, news_fetcher, auth_login, …
  adapters/      the outside world — persistence, http, agent, browser, files, tokens
  interface/     typer only; the root app and the one Console
  config.py      settings; the one module both sides may read
  data/          the Mantra schema grid and legality matrix, as package data
```

The rule that pays for itself: nothing in `domain/` may reach sqlalchemy,
Playwright, httpx, the agent SDK, typer, rich, the settings or the CLI. That is why
the default test tier runs in five seconds, opens no socket and makes no agent call.

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
