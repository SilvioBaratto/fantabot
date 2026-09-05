# CLAUDE.md — `app/` (fantabot-app)

Guidance for Claude Code when working in `app/`. See the repo-root `SPEC.md` and
`tasks/plan.md` for the full spec and plan.

## What this is

`fantabot-app` — a local, single-user web UI over the `fantabot` project. One command
(`fantabot-app`) provisions Postgres (no Docker), runs the FastAPI adapter, and serves the
compiled Angular bundle on one port. Installed with **uv**; the only prerequisite is `uv`
itself (Postgres ships inside the `pixeltable-pgserver` wheel; the frontend is compiled
into the wheel, so end users need no Node).

There is **no Docker** (the compose/Dockerfile scaffold was removed), no auth/JWT of its
own, no BAML. The clean architecture lives in `fantabot`; this is a thin adapter over it.

## Layout

```
app/
  pyproject.toml            # the fantabot-app package (hatchling); fantabot = path dep
  uv.lock                   # committed — reproducible install
  fantabot_app/             # ONE importable package
    cli.py                  # Typer launcher: setup / up / stop / doctor (up = default)
    paths.py                # ~/.fantabot/{pgdata,logs}
    server.py               # serve API + SPA on one port, open browser
    doctor.py               # environment checks
    provisioner/            # postgres (pixeltable_pgserver) + migrate + chromium
    api/                    # the FastAPI adapter (was top-level `app`, renamed in R1b)
      main.py  infrastructure/{settings,database,jobs,orm}  v1/{router,endpoints}  schemas/
      tests/                # api tests (pytest)
    web/                    # compiled Angular bundle (git-ignored build artifact; in the wheel)
  frontend/                 # Angular 21 (Tailwind v4, signals, standalone, OnPush)
  scripts/build_frontend.py # ng build -> fantabot_app/web (run before install / in CI)
  tests/                    # launcher + fitness + doctor tests
```

## Commands

```bash
# Python — dedicated venv (keep the fanta conda env fantabot-only)
uv venv                                   # app/.venv
uv pip install -e ".[dev]"
app/.venv/Scripts/python -m pytest        # from app/ : launcher + api tests (zero sockets;
                                          #   integration + real-DB tests are -m integration)
app/.venv/Scripts/python -m ruff check fantabot_app tests
app/.venv/Scripts/python -m mypy fantabot_app   # strict; tests + scaffold main.py excluded

# Frontend
cd frontend && npm ci && npm start        # dev server (dev only)
python scripts/build_frontend.py          # build + stage into fantabot_app/web for the wheel
cd frontend && npx ng test --watch=false  # vitest
```

## Rules

- **One DB, one engine.** Every session comes from `fantabot.adapters.persistence.database_manager`
  (`get_db` in `api/infrastructure/database.py`). The API adapter builds no `create_engine`/
  `sessionmaker` — enforced by `tests/test_fitness.py` (A6). The provisioner's transient
  admin engine (CREATE DATABASE) is the one documented exception.
- **Degrade open.** Read endpoints never 500 on a missing token/DB — they return an empty/
  not-connected state, like the CLI.
- **No secret leaks.** The app never reads a plaintext token or calls decrypt — fantabot
  does, internally (A7 fitness test). No token in any response.
- **Thin adapter.** Endpoints call fantabot use cases/repos; no re-added domain/application
  hexagon here.
- **The app never acts. It reads, and it watches.** No live bid, no lineup submission —
  and that is now a settled decision rather than a v1 deferral: the operator chose "the
  CLI bids, the app watches", and the app is loopback-only. The bidding locks
  (`FANTABOT_AUTO_ACT` + `--arm`, first Ctrl-C disarms) are a terminal contract a browser
  tab cannot reproduce — a closed tab would leave a bidding thread running with nobody
  attached.
  The fitness test bans the **acting** names: `teamLineup_submit(`, `decide_bid(`,
  `place_raise(`, `run_bid_loop(`, `RoomTracker(`. It used to ban the string
  `application.asta_room`, which is the module that also holds the read-only
  `resolve_room` and `RoomFrame` — so a room *viewer* failed the guard while
  `rtdb.place_raise` and `room.run_bid_loop`, the two functions that actually spend
  credits, were absent from the list and passed. The guard banned the viewer and
  permitted the bidder; it now does the opposite.
- **A FantaLab bearer never enters app code.** `rest.fetcher_from(store)` and
  `LiveAuctionsClient.from_store(store)` resolve it inside the adapter and keep it in a
  closure, mirroring `apileague.auth_headers(league_id, store=...)`. Callers hand over the
  store. This is not covered by the A7 fitness test — that scans only for
  `load_plaintext` and `.decrypt(`, and `FantalabStore.load()` trips neither — so it is a
  rule kept by review, which is why it is written here.
- **Actions run as jobs.** Long/interactive use cases (login, sync, news) run on the
  in-process job runner (`api/infrastructure/jobs.py`); the UI polls `GET /jobs/{id}`.
- **The bundled server's lifetime is explicit.** `PostgresProvisioner` passes
  `cleanup_mode=None` on every path, so Postgres outlives whatever started it and only
  `fantabot-app stop` / `fantabot-app db stop` takes it down. pgserver's default is
  `'stop'` — an atexit hook that stops the server when the last handle-holding process
  exits — which would make `db start` print a DSN to a server that died with the command.
  A fitness test reads the AST and fails on any `get_server(` call without an explicit
  `cleanup_mode`, because `get_server` caches per pgdata and *ignores* the argument on a
  cache hit: one bare call anywhere decides the mode for the whole process.
- **An exported `FANTABOT_DATABASE_URL` wins, and `.env` does not.** `start()` returns it
  and provisions nothing. The asymmetry is the point: an export is an instruction from the
  operator, while a `.env` found by whatever the working directory happens to be is the
  mechanism that put the CLI and the app on two different databases. `db stop`,
  `db status` and `db create` ignore it and address `~/.fantabot/pgdata` — `db start`
  prints an `export` line the operator pastes, and a `db stop` that honoured it would be a
  no-op in exactly that shell.
- **Disconnect purges; reconnect restores.** `DELETE /auth/league/{id}` removes the lega
  whole, across six tables — removing the token alone left it on every screen but the one
  that said it was disconnected. Reconnecting the account brings the lega back (its data
  on the next sync) and **that is correct**: the operator reconnected the account, so a
  durable exclusion would be a second, invisible piece of state. The CLI's `fantabot auth
  forget --league <id>` deliberately still removes the token alone — its one-row-at-a-time,
  no-`--all` contract is published in `README.md` and pinned by tests.
- **Headed login stays manual.** `POST /auth/login` opens the real browser and the user
  signs in by hand; a per-job gate + `.../confirm` is the web "press Enter".
```
