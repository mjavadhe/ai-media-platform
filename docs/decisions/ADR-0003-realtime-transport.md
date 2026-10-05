# ADR-0003: Realtime Transport

- Status: Proposed
- Date: 2026-10-05

## Context

Spec section 18 requires the UI to update without manual refreshes for: GPU status, utilization, VRAM, temperature, power, jobs, queue, worker health, logs, and progress. Spec section 18 names WebSockets, Server-Sent Events (SSE), or equivalent. The backend is FastAPI (ADR-0001). The dev stack must stay within ~4 GiB RAM, and the first deployment is a single server (spec section 69).

## Options considered

1. **Server-Sent Events (SSE)**
   - Pros: one-way server-to-client is exactly what monitoring needs; automatic reconnection and last-event-id resume built into browsers; works through plain HTTP proxies; trivial to implement in FastAPI (`StreamingResponse`); very low server overhead; no extra dependencies.
   - Cons: one-way only (client commands still use the REST API, which is fine — spec section 56 is API-first); some very old proxies buffer streaming responses (mitigated by `X-Accel-Buffering: no`).

2. **WebSockets**
   - Pros: bidirectional; lower per-message overhead; good for future interactive features (e.g. live prompt assistant).
   - Cons: connection state must be managed server-side (connection registry, fan-out pub/sub); more complex reconnection/backfill logic; higher memory per connected client; operational complexity not justified by current requirements.

3. **Polling**
   - Pros: simplest possible.
   - Cons: violates the spirit of spec section 18 (real-time), wastes bandwidth, scales poorly with many clients; rejected.

## Decision

**Server-Sent Events** for all server-push monitoring streams (GPU metrics, job progress, queue, logs, node health). Client-to-server actions (create job, cancel, retry) use the REST API (spec section 56). WebSockets are deferred: if a future requirement needs bidirectional streaming, the SSE endpoint can be complemented by a WebSocket endpoint behind the same event bus without changing the UI's data model.

## Consequences

- Easier: minimal server state, no connection registry, natural fit with FastAPI, cheap to run in dev.
- Harder: none for current requirements; a future WebSocket addition is a small, isolated extension.
- Must verify later: proxy behavior in the target deployment environment (Phase 10).
