# 19 — API Specification

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md) · ADRs: [ADR-0001](../decisions/ADR-0001-backend-framework.md), [ADR-0003](../decisions/ADR-0003-realtime-transport.md)

## Conventions

- **Versioning:** URL-prefix versioning — `/api/v1/...`. Breaking changes bump the minor version (`/api/v2`); additive changes do not.
- **Format:** JSON request/response (`application/json; charset=utf-8`), except file upload/download (`multipart/form-data`, raw bytes).
- **Auth:** session cookie (UI) or `Authorization: Bearer <token>` (API tokens and worker access credentials). All endpoints except `/health`, `/ready`, `/api/v1/auth/login`, and `POST /api/v1/workers/register` (which authenticates with a one-time registration token instead — see `18-worker-protocol.md`) require auth.
- **OpenAPI:** the spec is generated from the FastAPI app (spec section 57); the frontend client is generated from it. This document is the human-readable companion.

## Error format

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human-readable summary",
    "details": [ { "field": "prompt", "issue": "required" } ],
    "request_id": "uuid"
  }
}
```

- HTTP status codes are meaningful: `400` validation, `401` unauthenticated, `403` forbidden, `404` missing, `409` conflict (e.g. GPU already allocated), `422` domain rule violation, `429` rate-limited, `500` internal.
- `code` is a stable machine-readable enum; `message` may change.
- `request_id` correlates with logs (spec section 48).

## Pagination

- List endpoints: `?page=1&page_size=50` (max 200), or cursor-based for high-churn collections (logs, metrics).
- Response: `{ "items": [...], "page": 1, "page_size": 50, "total": 1234 }` (cursor: `{ "items": [...], "next_cursor": "..." }`).
- Sorting: `?sort=-created_at` (prefix `-` for descending); allowed sort fields are endpoint-specific.

## Filtering

- Query params per resource (e.g. `?status=running&node_id=...&gpu_id=...&project_id=...`), combinable; documented per endpoint in the generated OpenAPI.

## Endpoint list (spec section 56)

### Auth (`/api/v1/auth`)
- `POST /login` · `POST /logout` · `GET /me` · `POST /tokens` (create API token) · `DELETE /tokens/{id}`

### Projects / Sequences / Shots
- `GET|POST /api/v1/projects` · `GET|PATCH|DELETE /api/v1/projects/{id}`
- `GET|POST /api/v1/projects/{id}/sequences` · `GET|PATCH|DELETE /api/v1/sequences/{id}`
- `GET|POST /api/v1/sequences/{id}/shots` · `GET|PATCH|DELETE /api/v1/shots/{id}` · `POST /api/v1/sequences/{id}/shots/batch` (spec section 31)
- `GET /api/v1/projects/{id}/history` (generation history, spec section 20)

### Assets (characters, locations, references — spec sections 21-24)
- `GET|POST /api/v1/assets` · `GET|PATCH|DELETE /api/v1/assets/{id}`
- `GET|POST /api/v1/assets/{id}/versions` · `GET /api/v1/assets/{id}/versions/{vid}`
- `POST /api/v1/assets/{id}/references` (upload via storage port) · `GET /api/v1/assets/{id}/references`
- `GET|POST /api/v1/characters` · `GET|POST /api/v1/locations` (typed views over assets)

### Image / Video Generation
- `POST /api/v1/images/generate` (text-to-image, image-to-image, inpainting, outpainting, upscaling — per workflow schema, spec section 25)
- `POST /api/v1/videos/generate` (Phase 8)
- `POST /api/v1/shots/{id}/generate` (shot-driven generation, spec section 31)
- `GET /api/v1/shots/{id}/outputs` · `PATCH /api/v1/outputs/{id}/status` (PENDING/SELECTED/REJECTED/FINAL/ARCHIVED, spec section 88)

### Jobs
- `GET|POST /api/v1/jobs` · `GET /api/v1/jobs/{id}` · `GET /api/v1/jobs/{id}/attempts`
- `POST /api/v1/jobs/{id}/cancel` · `POST /api/v1/jobs/{id}/retry` · `POST /api/v1/jobs/{id}/pause` · `POST /api/v1/jobs/{id}/resume` · `PATCH /api/v1/jobs/{id}/priority` (spec section 55)
- `GET /api/v1/jobs/{id}/progress` (current progress; SSE stream for live)

### GPU Cluster
- `GET /api/v1/nodes` · `GET /api/v1/nodes/{id}` · `PATCH /api/v1/nodes/{id}` (maintenance mode)
- `GET /api/v1/gpus` · `GET /api/v1/gpus/{id}` · `GET /api/v1/gpus/{id}/metrics` (current + history, spec section 19)

### Models / Workflows
- `GET|POST /api/v1/models` · `GET|PATCH|DELETE /api/v1/models/{id}` · `GET /api/v1/models/{id}/versions`
- `POST /api/v1/models/{id}/install` · `POST /api/v1/models/{id}/validate` · `POST /api/v1/models/{id}/enable` · `POST /api/v1/models/{id}/disable` · `POST /api/v1/models/{id}/rollback` (spec section 38)
- `GET|POST /api/v1/workflows` · `GET|PATCH|DELETE /api/v1/workflows/{id}` · `GET /api/v1/workflows/{id}/versions`

### Logs / Metrics
- `GET /api/v1/logs` (filters: level, service, node, gpu, job, project, time range — spec section 49)
- `GET /api/v1/metrics` (filters: name, node, gpu, time range)

### Storage
- `POST /api/v1/uploads` (multipart → storage port) · `GET /api/v1/files/{id}` (redirect/proxy) · `DELETE /api/v1/files/{id}`

### Users / Admin
- `GET|POST /api/v1/users` · `GET|PATCH|DELETE /api/v1/users/{id}` · `POST /api/v1/users/{id}/roles`
- `GET /api/v1/audit` (spec section 61)

### Realtime (SSE — ADR-0003)
- `GET /api/v1/stream/gpus` · `GET /api/v1/stream/jobs` · `GET /api/v1/stream/logs` · `GET /api/v1/stream/nodes`
- All support `Last-Event-ID` resume; events are the same domain events the UI renders.

### Worker protocol endpoints (see `18-worker-protocol.md`)
- `POST /api/v1/workers` (admin; issues one-time registration token) · `POST /api/v1/workers/register` (consumes one-time token, issues credentials) · `POST /api/v1/workers/{id}/credentials` (renew access credentials) · `POST /api/v1/workers/{id}/heartbeat` (includes active claim IDs → lease renewal) · `POST /api/v1/workers/{id}/jobs/claim` (creates claim/lease) · `POST /api/v1/workers/{id}/claims/{claim_id}/renew` · `POST /api/v1/jobs/{id}/progress` · `POST /api/v1/jobs/{id}/complete` · `POST /api/v1/jobs/{id}/fail`

## Rate limits and quotas

- Auth endpoints: strict rate limit. Job creation: per-user quota (configurable). Generation endpoints: bounded by queue backpressure (HTTP 429 when the user's queued jobs exceed the limit).
