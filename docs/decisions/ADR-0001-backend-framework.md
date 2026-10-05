# ADR-0001: Backend Framework

- Status: Proposed
- Date: 2026-10-05

## Context

The platform needs a Python backend that serves the REST API (spec section 56), OpenAPI docs (spec section 57), authentication (spec section 58), and the realtime monitoring channel (spec section 18). Spec section 66 names FastAPI and Django as candidates and requires separation of API, domain logic, persistence, scheduling, workers, integrations, and infrastructure. The dev machine has ~14 GiB RAM with a ~4 GiB budget for the full stack (AGENTS.md), so framework weight matters. The backend must also host the Orchestrator and Scheduler modules (spec sections 11-12) and expose WebSocket/SSE endpoints.

## Options considered

1. **FastAPI**
   - Pros: async-native (one process can serve API + scheduler + SSE fan-out), automatic OpenAPI generation (spec section 57), Pydantic validation, small memory footprint, first-class WebSocket/SSE support, easy to keep domain logic in separate modules (spec section 66), large ecosystem.
   - Cons: async requires discipline (sync DB drivers need threadpool or async ORM), younger admin/permissions ecosystem than Django.

2. **Django (+ Django REST Framework)**
   - Pros: batteries-included admin, ORM, auth, migrations; mature RBAC packages; huge ecosystem.
   - Cons: synchronous by default (ASGI possible but the ecosystem is sync-oriented), heavier process/memory footprint, admin UI conflicts with the "no default Django admin" rule (spec section 15) and would need to be disabled/replaced anyway, ORM couples domain to persistence unless carefully layered.

3. **Other (Litestar, Flask, etc.)**
   - Not evaluated in depth: smaller talent/integration pool, weaker async story (Flask) or smaller ecosystem (Litestar) for a long-lived production platform.

## Decision

**FastAPI.** It best satisfies the spec section 66 separation requirements (clean module boundaries via dependency injection), generates OpenAPI automatically (spec section 57), supports the realtime transport natively (spec section 18), and has the smallest operational footprint for the 4 GiB dev budget. Domain logic lives in a `domain/` layer with no framework imports; FastAPI is only the HTTP adapter.

## Consequences

- Easier: API-first design, OpenAPI docs, realtime endpoints, small footprint, clear layering.
- Harder: async discipline required; auth/RBAC must be built on FastAPI security primitives (no Django admin to fall back on — which spec section 15 forbids anyway).
- Must verify later: none specific to the framework; integration tests run against the app with a test database.
