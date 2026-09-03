# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Full-stack application with **FastAPI**, **Angular**, and **Docker**, with **bearer-token** authentication.

## Build & Run Commands

```bash
# Run full stack with Docker
docker compose up -d --build

# Stop all services
docker compose down
```

## API Template (`api/`)

FastAPI application with clean / hexagonal architecture (four layers):

| Layer | Purpose |
|-------|---------|
| `app/domain/` | Pure core: `entities/` (dataclasses), `ports/` (repository interfaces), `services/`, `exceptions.py` — no framework/ORM imports |
| `app/application/` | Use-case orchestration: `services/` (depend on domain ports), `dto/`, `commands/` — imports domain only |
| `app/infrastructure/` | The only tech layer: `settings.py`, `config.py` (walk-up `.env` loader), `database.py`, `orm/` (SQLAlchemy), `repositories/` (port adapters), `audit.py` |
| `app/api/` | HTTP surface: `deps.py` (composition root), `handlers.py`, `schemas/` (Pydantic), `middleware/`, `v1/router.py` + `v1/endpoints/` |
| `baml_src/` | LLM function definitions (regenerate client with `baml-cli generate`) |

Dependencies point strictly inward: `api → application → domain`, `infrastructure → domain`. Enforced by an AST fitness test (`api/tests/unit/test_architecture.py`).

**Sync vs async**: DB endpoints are sync `def` (offloaded to a threadpool with the sync `Session`); `async def` is reserved for BAML/LLM calls. See `api/.claude/CLAUDE.md` for the full rule.

### Mapping from the FastAPI tutorial

The official FastAPI "Bigger Applications" tutorial organizes path operations into a `routers/` package where each router file also holds inline DB queries and business logic. This scaffold keeps the routers at `app/api/v1/endpoints/` (aggregated by `app/api/v1/router.py`, all under the `/api/v1` prefix) and splits what the tutorial keeps inline across the four hexagonal layers: router (`app/api/v1/endpoints/`) → application service (`app/application/services/`) → domain port (`app/domain/ports/`) implemented by an infrastructure repository (`app/infrastructure/repositories/`) → ORM model (`app/infrastructure/orm/`) → Pydantic schema (`app/api/schemas/`).

Unlike the tutorial's flat layout, **this is a real restructure — the code is physically split across `domain`/`application`/`infrastructure`/`api`, not just conceptually.**

## Token auth (`--auth token`)

`POST /auth/validate` is a validity **probe**: it returns **HTTP 200 with `authenticated=false`** for an invalid token, because both outcomes are successful *answers* to "is this token valid?". **401 is reserved for guarded endpoints that refuse access** (`/auth/me`, item writes), never for the probe. The supabase variant has **no** `/auth/validate` endpoint; token validity is resolved server-side via JWT / `supabase.auth.getUser()` instead.

## Frontend Template (`frontend/`)

Angular 21 with Tailwind CSS (standalone components, signals, native control flow, OnPush, `inject()`). See `frontend/.claude/CLAUDE.md` for the full conventions.

## Docker Services

| Service | Port | Description |
|---------|------|-------------|
| `db` | 5433:5432 | PostgreSQL 16 |
| `api` | 8000:8000 | FastAPI with hot reload |
| `frontend` | 4200:80 | Angular + nginx |
| `adminer` | 8080:8080 | Database admin UI |
