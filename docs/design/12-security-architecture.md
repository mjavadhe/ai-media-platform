# 12 — Security Architecture

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md)

## Threat model (spec section 60)

Command injection, path traversal, arbitrary file execution, unauthorized GPU access, unauthorized model execution, credential leakage, unsafe API access, malicious workflow execution, privilege escalation.

## Authentication (spec section 58)

- Session-based auth (HTTP-only cookies) for the UI; API tokens for programmatic access.
- Passwords hashed with a modern KDF (argon2id); login rate-limited.
- Worker-to-control-plane authentication: per-worker tokens (mTLS where deployment supports it) — see `18-worker-protocol.md`.

## Authorization (spec sections 58-59)

- Roles: `ADMIN, OPERATOR, ARTIST, VIEWER` (spec section 59), stored as data.
- Granular permissions (project access, asset management, generation, model management, GPU management, worker management, system configuration) enforced in the domain layer — never only in the UI.
- Object-level checks: users only see projects/assets they are granted access to.

## API security (spec sections 56-57, 60)

- All endpoints authenticated except health checks.
- Input validation via Pydantic schemas at the API boundary; parameterized queries only (no string-built SQL).
- Rate limiting on auth and job-creation endpoints.
- CORS locked to the configured frontend origin.

## Execution security (spec section 60)

- **Workflows:** validated against their input schema before execution; engine adapters run with least privilege; no arbitrary code execution paths from user input.
- **Storage:** all file access through the storage port with server-side key construction — user input never becomes a filesystem path directly (path traversal defense); uploads validated by type/size.
- **Models:** only registry-installed, checksum-verified models may be executed (spec section 38).
- **GPU access:** the scheduler allocates GPUs only to validated jobs; users cannot address GPUs directly.

## Secrets (spec section 96, rule 12)

- Secrets only via environment variables or a secret manager — never in code, config files, or the DB in plaintext.
- Config externalized (env vars + config files); a `config.example` documents every variable without values.

## Audit (spec section 61)

Sensitive operations (model install/remove, GPU state changes, job control, user/role changes, project deletion) write `audit_entries`: user, action, resource, previous value, new value, timestamp, IP.

## Dependency and supply-chain hygiene

- Locked dependencies (uv, ADR-0005); CI runs security checks on the lockfile (spec section 97).
- Model and container sources are configurable mirrors with checksum verification (spec sections 74-75).
