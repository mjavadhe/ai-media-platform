# 16 — Phase 1 Implementation Plan

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md), [spec §98](../spec.md)

Phase 1 — Foundation (spec section 98): Repository, Backend, Frontend, Database, Authentication, Basic API, Configuration System.

## Steps (each ends with tests + docs + review)

1. **Repository bootstrap**
   - `pyproject.toml` + `.python-version` (3.12) + `uv.lock` (ADR-0005)
   - Repo structure per `03-repository-structure.md`
   - CI skeleton: lint, typecheck, unit tests (mock backend only)

2. **Configuration system**
   - Env-var config loader with validation; `config.example` (no secrets)
   - `GPU_BACKEND=auto|nvml|mock` selection (ADR-0007)

3. **Database**
   - Postgres connection + Alembic setup
   - Initial migrations: users, roles, permissions, projects (minimal)

4. **Authentication**
   - User model, password hashing (argon2id), session login, API tokens
   - RBAC skeleton: four roles (spec section 59) with permission checks in the domain layer

5. **Backend API skeleton**
   - FastAPI app with health/ready endpoints
   - Error format + pagination conventions (see `19-api-specification.md`)
   - OpenAPI generation verified

6. **Frontend skeleton**
   - Vite + React + TS app, login page, empty dashboard shell
   - API client generated from OpenAPI; i18n + RTL scaffolding (ADR-0004)

7. **Basic domain + API**
   - Projects CRUD (API + UI) — the vertical slice proving the architecture

8. **Docs**
   - README, development setup guide (uv, Postgres, mock backend)
   - Update `docs/verification-checklist.md` with Phase 1 items

## Phase 1 exit criteria

- CI green: unit + integration tests against Postgres test DB and mock GPU backend
- Login + project CRUD work via UI and API
- OpenAPI docs generated and reviewed
- Docs updated; summary of what changed and what is NOT done

## Explicitly NOT in Phase 1

GPU discovery, job queue, scheduler, worker execution, models, workflows, image generation, realtime dashboards beyond a static shell.
