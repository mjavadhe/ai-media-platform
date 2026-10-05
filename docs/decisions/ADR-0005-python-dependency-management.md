# ADR-0005: Python Version & Dependency Management

- Status: Proposed
- Date: 2026-10-05

## Context

AGENTS.md requires a pinned Python version via uv or a virtualenv — not the system Python (Ubuntu 26.04 is very new). The platform has multiple Python consumers: backend, worker, and (later) engine adapters. ADR-0006 pins PyTorch/CUDA for the worker; the pin must be reproducible across dev, CI, and the target server. The dev stack must fit in ~4 GiB RAM.

## Options considered

1. **uv (pin + lockfile)**
   - Pros: single tool for Python version management, virtual environments, dependency resolution, and locking; `uv.lock` gives fully reproducible installs; fast; one workflow for backend and worker; supports dependency groups (dev, test) and constraints.
   - Cons: team must adopt uv (small learning curve); lockfile must be committed and respected in CI.

2. **pip + requirements.txt + pyenv/virtualenv**
   - Pros: universally known; no new tooling.
   - Cons: no transitive lockfile by default (requirements.txt pins are manual and incomplete); Python version management is a separate tool (pyenv); easier to get environment drift between dev and the target server.

3. **Poetry / PDM**
   - Pros: lockfiles and environment management.
   - Cons: slower/less unified than uv for this use case; extra abstraction without compensating benefit.

## Decision

**uv.** Pin the Python version in `.python-version` (3.12 — supported by every PyTorch version in the ADR-0006 viable range, 2.7-2.14), declare dependencies in `pyproject.toml` with dependency groups, commit `uv.lock`, and use `uv sync` in dev, CI, and deployment. The worker's PyTorch/CUDA pin (ADR-0006) is a separate locked extra so the backend environment stays light.

## Consequences

- Easier: reproducible environments everywhere; one command to recreate the dev environment; clean separation of backend vs worker heavy deps.
- Harder: everyone uses uv; CI must run offline-capable installs from the lockfile (mirror configuration per hardware.md section C).
- Must verify later: `uv sync` works against the configured package mirror on the target server (Phase 1 setup).
