# 10 — Workflow Architecture

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md)

## Workflow registry (spec section 40)

Workflows are first-class, versioned entities, separate from models and engines:

```text
Workflow (cinematic-image-v1)
  ├── Engine: ComfyUI | diffusers | custom (spec §42)
  ├── Model: reference to a model version (optional)
  ├── Inputs: typed schema (prompt, negative_prompt, seed, reference, resolution, ...)
  └── Versions: immutable definitions; jobs pin a workflow version
```

- `workflows` → `workflow_versions` (spec section 40: workflows must be versioned).
- The inputs schema drives the UI: the generator form exposes **only the parameters the selected workflow supports** (spec section 26).
- Workflows are engine-specific in their definition but the registry stores them neutrally; the engine adapter translates.

## Engine abstraction (spec section 42)

```text
EngineAdapter (interface)
  ├── validate(job) -> validation result
  ├── run(job, progress_callback) -> JobResult
  └── cancel()

  ├── ComfyUIAdapter      (Phase 5 — spec §41)
  ├── DiffusersAdapter
  └── CustomPythonAdapter (future)
```

The core platform never imports ComfyUI or diffusers directly — only adapters do (spec section 42: no tight coupling to ComfyUI).

## ComfyUI integration (spec section 41)

```text
Web UI → API → Orchestrator → Workflow Registry → ComfyUI Adapter → ComfyUI → GPU
```

- The adapter resolves the pinned workflow version to a ComfyUI graph (template + parameters), submits it, streams progress back, and collects outputs.
- Users never interact with raw ComfyUI for normal operation (spec section 41).
- ComfyUI runs on the worker node; the adapter talks to it over its API. ComfyUI's own PyTorch pin must be compatible with ADR-0006 — **needs verification** (V-006).

## Execution flow

1. Job created with model version + workflow version + parameters.
2. Scheduler allocates a GPU whose capabilities cover the workflow's requirements.
3. Worker loads the engine adapter for the workflow's engine.
4. Adapter validates parameters against the workflow's input schema.
5. Adapter executes, streaming progress; outputs are written through the storage port.
6. Job completed with full reproducibility metadata (spec section 47): exact model + version, workflow + version, prompt, seed, parameters, references, engine version, runtime info.

## Workflow parameters and reproducibility (spec sections 46-47)

Every generation records the exact inputs and versions, enabling reproduction and the version history UI (spec sections 46, 87).
