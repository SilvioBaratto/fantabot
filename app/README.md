# fantabot-app

A local, single-user web UI over [`fantabot`](../). One command provisions its own
Postgres (no Docker), runs the FastAPI adapter, and serves the compiled Angular app on a
single port — then opens your browser. It's a thin adapter over `fantabot`: it reads your
leghe, rosters, credits, news sentiment, asta plan, target prices, legality and lineup
**preview**, and runs the safe actions (connect account, lega sync, news fetch). It never
places a live bid or submits a lineup, and it has no login of its own.

Built with **FastAPI** + **Angular**. No Docker, no separate database to install, no Node
for end users.

## Install & run

The tools you install by hand are **git** and [**uv**](https://docs.astral.sh/uv/).
Everything else — the Python runtime, PostgreSQL, and the compiled frontend — comes with
the app. Nothing is published, so you install from a clone of this repo:

```bash
git clone https://github.com/SilvioBaratto/fantabot.git
cd fantabot

uv tool install ./app     # installs the `fantabot-app` command (fantabot resolved from ../)
fantabot-app setup        # provisions Postgres (bundled PG18), migrates, installs chromium
fantabot-app              # starts everything and opens http://127.0.0.1:8000
```

That's it. `fantabot-app setup` is safe to re-run; `fantabot-app` is the everyday launch.

> **Fresh clone?** The compiled UI (`fantabot_app/web`) is a build artifact that isn't
> committed. Released builds bundle it automatically, but if you installed from a fresh
> checkout, build it once first (needs Node + npm):
> ```bash
> python app/scripts/build_frontend.py     # ng build -> fantabot_app/web, then reinstall
> ```
> Until you do, the app boots and serves a placeholder page instead of the UI.

## Everyday use

```bash
fantabot-app            # up (the default): start Postgres if needed, serve UI, open browser
fantabot-app up         # same as above, explicitly
fantabot-app stop       # stop the local Postgres the launcher started
fantabot-app doctor     # health report: python, fantabot, Postgres, database, chromium
fantabot-app --help
```

`fantabot-app doctor` is the first thing to run when something is off — it checks each
piece and tells you which one to fix (it exits non-zero if any check fails).

Everything the app stores lives under `~/.fantabot/` (`pgdata/` is the database,
`logs/` the logs). Postgres binds to `127.0.0.1` only — this is a local appliance, not a
server.

## Development

End users never need this section. To work on the app:

### Backend (FastAPI)

```bash
cd app
uv venv                          # creates app/.venv
uv pip install -e ".[dev]"

fantabot-app setup               # provision the database once (exports FANTABOT_DATABASE_URL)
uvicorn fantabot_app.api.main:app --reload     # dev server on :8000

# Tests (default tier opens zero sockets; real-Postgres tests are opt-in)
python -m pytest
python -m pytest -m integration  # spins up a real bundled Postgres

# Quality (mirrors fantabot)
ruff check fantabot_app tests
mypy fantabot_app                # strict
```

The database, models, and migrations belong to `fantabot`; the app creates no schema and
runs no second engine — every session comes from `fantabot`'s `database_manager`.

### Frontend (Angular)

```bash
cd app/frontend
npm ci
npm start                        # ng serve on http://localhost:4200 (proxies /api to :8000)
npm run build                    # production build
npx ng test --watch=false        # vitest
```

The end-user app doesn't run this dev server. For distribution the frontend is compiled
into the wheel:

```bash
python app/scripts/build_frontend.py     # ng build -> fantabot_app/web (bundled by hatchling)
```

`uv.lock` is committed for reproducible installs.

## More

- `app/CLAUDE.md` — layout, rules, and the wiring decisions.
- `../README.md` — the underlying `fantabot` CLI.

## License

MIT
