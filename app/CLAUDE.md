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
- **v1 boundary.** No live bid, no lineup submission (fitness test guards the wiring).
- **Actions run as jobs.** Long/interactive use cases (login, sync, news) run on the
  in-process job runner (`api/infrastructure/jobs.py`); the UI polls `GET /jobs/{id}`.
- **Headed login stays manual.** `POST /auth/login` opens the real browser and the user
  signs in by hand; a per-job gate + `.../confirm` is the web "press Enter".
```
