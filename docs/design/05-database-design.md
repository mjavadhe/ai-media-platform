# 05 — Database Design

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md) · ADR: [ADR-0008](../decisions/ADR-0008-database.md)

**Related design docs:** the creative-entity data model (projects, sequences, shots, assets, generated media/versions, approval, templates, tags) is specified in `21-asset-project-shot-data-model.md`; deployment topology is specified in `20-deployment-architecture.md`. This document covers the full schema; those documents define entity behavior.

## Principles

- PostgreSQL, normalized, indexed, migration-based (Alembic), version-controlled (spec section 62).
- **No large media in the DB** (spec section 100, rule 11): media tables hold metadata + storage keys; bytes live behind the storage abstraction.
- Extensible metadata via JSONB columns where the schema is inherently open-ended (model params, job parameters), with validated core columns for queryable fields.

## Entity-Relationship (core entities, spec section 63)

```text
users ──< user_roles >── roles ──< role_permissions >── permissions
projects ──< sequences ──< shots
projects ──< project_assets >── assets (polymorphic: character/location/prop/...)
assets ──< asset_versions
assets ──< reference_media (links to storage objects)
gpu_nodes ──< gpu_devices ──< gpu_capabilities
jobs ──< job_attempts
jobs >── models >── model_versions >── model_installations
jobs >── workflows >── workflow_versions
jobs >── gpu_devices / gpu_nodes (where it ran)
storage_objects (metadata for media blobs)
log_entries · audit_entries · metrics
```

## Key tables (summary; full DDL arrives with Phase 1 migrations)

| Table | Notable columns | Indexes |
|-------|-----------------|---------|
| `users` | id, username, password_hash, is_active | username (unique) |
| `roles` / `permissions` / join tables | name, code | code (unique) |
| `projects` | id, name, description, status, created_by, created_at | created_by, status |
| `sequences` | id, project_id, name, position | (project_id, position) |
| `shots` | id, sequence_id, title, status, duration, params(JSONB) | (sequence_id), status |
| `assets` | id, project_id(nullable=global), type, name, tags(JSONB) | (project_id, type), GIN(tags) |
| `asset_versions` | id, asset_id, version, storage_key, metadata(JSONB) | (asset_id, version) |
| `gpu_nodes` | id, name, hostname, status, last_heartbeat_at | status, last_heartbeat_at |
| `gpu_devices` | id, node_id, uuid, name, vram_total, compute_capability, driver_version, status | node_id, uuid |
| `gpu_capabilities` | id, gpu_device_id, capability, source(registry/probe) | (gpu_device_id, capability) |
| `gpu_reservations` | id, job_id, node_id, gpu_device_id, worker_id(nullable), policy, scores(jsonb), state(RESERVED/BUSY/RELEASE_PENDING/RELEASED), created_at, updated_at | (job_id), (state) for reconciliation, unique (gpu_device_id) while state != RELEASED |

**Two GPU state domains with different owners** (enforced at the application/repo layer, see `08-scheduler-architecture.md` ownership boundary):

| Column | Domain | Writable by |
|--------|--------|-----------|
| `gpu_devices.status` | **Physical** GPU lifecycle | **Scheduler only.** Enforce with a repository method that no other component can reach. |
| `gpu_reservations.state` | **Logical** allocation/release tied to a job | Orchestrator may set `RESERVED/BUSY → RELEASE_PENDING` (request). Scheduler sets `RESERVED`/`BUSY` and completes `RELEASE_PENDING → RELEASED`. |

Release is exactly-once because `RELEASE_PENDING → RELEASED` is a conditional update (`WHERE state='RELEASE_PENDING'`), so a duplicate release affects zero rows. GPU availability is derived from `gpu_devices.status`; no allocation counter exists, so a double release cannot corrupt a count.
| `jobs` | id, project_id, sequence_id, shot_id, type, priority, status, model_version_id, workflow_version_id, params(JSONB), gpu_device_id, node_id, error, created_at, started_at, finished_at | status+priority+created_at (queue claim), (project_id), (gpu_device_id) |
| `job_attempts` | id, job_id, worker_id, gpu_device_id, attempt_no, status, error, started_at, finished_at | (job_id, attempt_no) |
| `job_claims` | id, claim_id, job_id, worker_id, attempt_id, expires_at, created_at | (job_id) unique while active, (claim_id) |
| `models` / `model_versions` | name, type, provider, repo, license, vram_requirement, precision, tasks(JSONB), engines(JSONB) | (type), (name) |
| `model_installations` | id, model_version_id, node_id, path, checksum, status, installed_at | (model_version_id, node_id) |
| `workflows` / `workflow_versions` | name, engine, inputs_schema(JSONB), version | (name, version) |
| `storage_objects` | id, backend, bucket, key, size, mime_type, checksum, owner_type, owner_id | (owner_type, owner_id), key |
| `log_entries` | ts, level, service, node_id, gpu_device_id, job_id, message, stack_trace | ts, (service), (job_id) |
| `audit_entries` | id, user_id, action, resource_type, resource_id, prev(JSONB), new(JSONB), ip, ts | (user_id), (resource_type, resource_id), ts |
| `metrics` | ts, service, node_id, gpu_device_id, name, value, labels(JSONB) | (name, ts), (gpu_device_id, ts) |

## Queue tables (Phase 1-3, ADR-0002)

The `jobs` table doubles as the queue: `status='queued'` rows are claimed via `SELECT ... FOR UPDATE SKIP LOCKED` ordered by `priority, created_at`; delayed jobs use `visible_at`; cancellation is a cooperative status flag. This is an implementation detail of the `JobQueue` port, not a separate schema.

## Conventions

- All tables: `id` UUID primary key, `created_at`/`updated_at` timestamps.
- Soft status enums as Postgres `ENUM` types or constrained strings (decision at migration time).
- JSONB for open-ended params/metadata; GIN indexes where queried.
- Migrations are forward-only in normal operation; rollbacks supported for recovery.
