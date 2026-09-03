# CLAUDE.md

Guidance for Claude Code when working on this FastAPI API.

## What this is

A thin local FastAPI adapter over the `fantabot` Python project. HTTP endpoints
here will call fantabot's use cases and repositories and reuse its database.
There is no auth, no BAML, no alembic, and no in-repo hexagonal domain/application
layering — that architecture lives in the `fantabot` project, not in this adapter.
Keep this surface small.

## Build & Run Commands

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload            # Run dev server (:8000)
pytest                                   # Run tests
```

## Layout

```
app/
├── main.py                 # App factory: load_configuration → logging → lifespan → CORS → router
├── infrastructure/
│   ├── config.py           # Walk-up .env loader (load_configuration)
│   ├── settings.py         # Settings (pydantic-settings, reads os.environ)
│   ├── database.py         # Engine + SessionLocal + get_db / init_db / close_db (sync SQLAlchemy)
│   └── orm/base.py         # Declarative Base for SQLAlchemy models
└── api/
    ├── schemas/            # Pydantic request/response schemas
    └── v1/router.py        # Aggregates endpoints under /api/v1
```

## Patterns

- **Adding routes**: create a module under `app/api/v1/endpoints/`, expose an
  `APIRouter`, and register it on `api_router` in `app/api/v1/router.py`. Everything
  lands under `/api/v1`.
- **DB sessions**: inject `Depends(get_db)` from `app.infrastructure.database`. It
  yields a sync `Session` and closes it. DB-backed path operations are sync `def`
  (offloaded to a threadpool); reserve `async def` for real async I/O.
- **Config**: `load_configuration()` runs before Settings are read, walking up to the
  project `.env`. Settings read `os.environ` only.
- **Tests**: `tests/conftest.py` overrides `get_db` with an in-memory SQLite session
  and drives the app through `TestClient`.
