# ADR-0004: Frontend Stack

- Status: Proposed
- Date: 2026-10-05

## Context

Spec section 15 requires a modern, responsive, component-based, professional web control panel (explicitly not default Django admin or basic forms). Spec section 65 names React, Next.js, TypeScript, or an equivalent production-grade choice, and requires the final choice to be justified. Spec section 15 also requires RTL-ready and localization-ready (Persian planned). The dev machine has ~14 GiB RAM with a ~4 GiB stack budget, so build tooling weight matters.

## Options considered

1. **React + Vite + TypeScript**
   - Pros: Vite dev server is fast and lightweight; React ecosystem is the largest for component libraries (tables, charts, dashboards); SPA is a natural fit for a control plane talking to a REST + SSE backend (ADR-0001, ADR-0003); RTL support via logical CSS properties / `dir="rtl"`; i18n via react-i18next or similar; no server-side rendering needed for an authenticated internal tool.
   - Cons: no SSR (irrelevant for an authenticated control plane); more initial setup than a meta-framework.

2. **Next.js + TypeScript**
   - Pros: file-based routing, SSR/SSG, large ecosystem, built-in API routes.
   - Cons: SSR adds server runtime complexity and memory for a UI that is a single authenticated SPA; API routes duplicate the FastAPI backend (spec section 56 is API-first — the backend is the API); heavier dev/build footprint; no requirement justifies SSR.

3. **Vue / Svelte / other**
   - Not selected: smaller pool of production dashboard components and hiring/integration ecosystem compared with React for this use case; no compensating advantage for the spec requirements.

## Decision

**React + Vite + TypeScript.** It is the lightest production-grade option that satisfies spec sections 15/65, matches the REST + SSE backend (ADR-0001, ADR-0003), and keeps the dev footprint small. RTL and i18n are handled at the component/CSS level (logical properties, `dir` attribute, i18n resource files) so Persian can be added without restructuring.

## Consequences

- Easier: fast dev loop, huge component ecosystem, simple deployment (static build served by the backend or a small web server).
- Harder: none material; chart/table components must be selected and kept dependency-light.
- Must verify later: none; frontend tests run in CI with the component test runner.
