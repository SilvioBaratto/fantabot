# fantabot API

A thin local FastAPI adapter over the `fantabot` Python project. It exposes
HTTP endpoints that will call fantabot's own use cases and repositories and
reuse its database. No auth, no BAML, no alembic, no hexagonal domain layers —
that architecture lives in the `fantabot` project itself, not here.

## Quick Start

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload    # http://localhost:8000
pytest                           # run tests
```

Docs are served at `/docs` (Swagger) and `/redoc` when `debug` is on;
`/openapi.json` carries the schema. `/` and `/health` are baseline liveness routes.

## Layout

```
app/
├── main.py                 # App factory: load config → logging → lifespan → CORS → router
├── infrastructure/
│   ├── config.py           # Walk-up .env loader (load_configuration)
│   ├── settings.py         # Settings (pydantic-settings, reads os.environ)
│   ├── database.py         # SQLAlchemy engine + SessionLocal + get_db / init_db / close_db
│   └── orm/base.py         # Declarative Base for models
└── api/
    ├── schemas/            # Pydantic request/response schemas
    └── v1/router.py        # Aggregates endpoints under /api/v1 (add modules here)
```

Add endpoint modules under `app/api/v1/endpoints/` and register them on
`api_router` in `app/api/v1/router.py`.
