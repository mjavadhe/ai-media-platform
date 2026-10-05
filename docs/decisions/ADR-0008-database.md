# ADR-0008: Database

- Status: Proposed
- Date: 2026-10-05

## Context

Spec section 62 requires a production-grade relational database, PostgreSQL preferred unless a strong technical reason exists. The schema must be normalized, extensible, indexed, migration-based, and version-controlled (spec section 62). Spec section 63 lists the core entities. ADR-0002 selects a Postgres-backed queue for Phase 1-3, which assumes Postgres. The dev stack must fit in ~4 GiB RAM.

## Options considered

1. **PostgreSQL**
   - Pros: spec-preferred; required by the Phase 1-3 queue choice (ADR-0002); excellent JSON support for extensible metadata; mature migrations (Alembic); strong ecosystem; runs comfortably in the RAM budget.
   - Cons: none material at this scale.

2. **MySQL / MariaDB**
   - Pros: familiar.
   - Cons: weaker JSON/extensibility story; no advantage over Postgres for this workload; not spec-preferred.

3. **SQLite**
   - Pros: zero infra.
   - Cons: not production-grade for concurrent writers (spec section 62); rejected.

## Decision

**PostgreSQL** (latest stable major available on the target OS). Migrations via Alembic, version-controlled in the repository. Connection access only through the persistence layer (spec section 66 separation).

## Consequences

- Easier: one store for business data and the Phase 1-3 queue; single backup story.
- Harder: none material.
- Must verify later: Postgres version availability on the target server (V-001).
