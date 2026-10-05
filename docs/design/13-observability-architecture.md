# 13 — Observability Architecture

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md)

## Logging (spec section 48)

- Structured logs (JSON) with fields: `Timestamp, Level, Service, Node, GPU, Job, Project, Model, Workflow, Message, Stack Trace`.
- Levels: `DEBUG, INFO, WARNING, ERROR, CRITICAL`.
- Every service and worker emits to stdout; a shipper (Phase 10) forwards to the log store. The `log_entries` table supports the Log Center UI (spec section 49): real-time (SSE), filtering by level/service/node/GPU/job/project, search, and time ranges.
- Correlation IDs: job id and request id propagate through all log lines.

## Metrics (spec section 50)

- Node: CPU, RAM, disk, network.
- GPU: utilization, memory, temperature, power (from `GpuBackend` — real or mock).
- Platform: queue size, job throughput, job failure rate, storage usage.
- Stored in the `metrics` table (time-series) with labels; retained per a configurable policy. Historical GPU metrics power the charts in spec section 19.

## Health (spec section 51)

- Every service and worker exposes `/health` (liveness) and `/ready` (readiness).
- Node states: `HEALTHY, DEGRADED, OFFLINE, UNHEALTHY, MAINTENANCE`; GPU lifecycle states: `AVAILABLE, RESERVED, BUSY, DRAINING, UNHEALTHY, OFFLINE, MAINTENANCE` (spec section 7); GPU health flag `ERROR` (spec section 51) is reported while the lifecycle state is `UNHEALTHY` — see `08-scheduler-architecture.md`.
- Heartbeat loss → node `OFFLINE` → automatic recovery flow (spec section 52; see `18-worker-protocol.md`).

## Realtime fan-out (spec section 18, ADR-0003)

- An in-process event bus (pub/sub) receives domain events (job state changes, GPU metrics ticks, log lines, heartbeats).
- SSE endpoints stream these to the UI. (Future multi-node option: a Redis pub/sub backplane behind the same interface — not a Phase 1-3 dependency; the queue remains Postgres-backed per ADR-0002.)
- The UI never polls for dynamic data; REST remains available for on-demand queries and all commands.

## Tracing and reproducibility (spec section 90)

- Every job records its full context (model, workflow, versions, parameters, GPU, node, engine version, runtime) — answering "what ran, where, why, with what, can it be reproduced" (spec section 90).
- OpenTelemetry tracing is a Phase 10 candidate; the event bus and log correlation provide the foundation.

## Alerting (Phase 10)

- Alert rules over metrics/logs (node offline, GPU error, queue depth, failure rate) — out of scope for MVP design but the data model supports it.
