# 21 — Asset / Project / Shot / Generation Data Model

Status: Proposed · Date: 2026-10-05 · Parent: [spec §106 item 13](../spec.md), [spec §20-32](../spec.md), [spec §46-47](../spec.md), [spec §79](../spec.md), [spec §84-89](../spec.md)

This document details the production data model for the creative entities. Table-level mapping is in `05-database-design.md`; this document defines the entities, their relationships, and their behavior.

## Entity overview

```text
Project ──< Sequence ──< Shot ──< Generation (job) ──< GeneratedMedia >── GeneratedMediaVersion
   │                                        │
   └──< Asset (polymorphic) >── AssetVersion │
   │                                        │
   └──< ReferenceMedia >── StorageObject     │
                                            │
PromptTemplate / StyleTemplate / CameraTemplate / LightingTemplate (versioned, reusable)
Tag (global, many-to-many on all taggable entities)
```

## Project (spec section 20)

- Top-level production entity: metadata, status, owner, timestamps.
- Contains sequences, shots, assets, generation history, exports.
- Projects are fully independent (spec section 20); cross-project reuse happens only through **global** assets (spec section 24).

## Sequence and Shot (spec sections 30-31, 84)

- `Sequence`: ordered group of shots within a project (position column).
- `Shot`: core production entity — title, description, duration, characters/locations/assets (references), prompt/negative prompt, camera/lens/movement, lighting/weather/time-of-day, style, model/workflow (optional pins), seed, status, generation history, outputs.
- Batch creation (spec section 31): `POST /sequences/{id}/shots/batch` creates N shots from a template + overrides.

## Assets (spec sections 21-24)

- **Polymorphic asset types:** character, location, prop, weapon, vehicle, building, creature, reference image/video, audio, other — the type list is data, not code (spec section 23).
- **Characters** (spec section 21): name, description, age, gender, appearance (face/hair/body/clothing/armor/accessories), personality, voice, reference media, versions, tags, metadata.
- **Locations** (spec section 22): name, description, architecture, environment, lighting, weather, time of day, season, reference media, versions, tags, metadata.
- **Versioning** (spec section 23): every asset has immutable `AssetVersion` rows (v1, v2, ...); each version stores its own metadata and storage references.
- **Shared vs project-specific** (spec section 24): `scope = GLOBAL | PROJECT`. Global assets are reusable across projects; project assets are private until explicitly promoted/shared.
- **References** (spec section 23): assets link to `ReferenceMedia` (images/videos) stored via the storage port; generation metadata preserves which references were used (spec section 83).

## Generated media, versions, and approval (spec sections 46-47, 86-88)

- Every generation produces `GeneratedMedia` linked to its shot (and project/sequence).
- **Versions** (spec section 46): each generation is an immutable version (`v001`, `v002`, ...) preserving full metadata: model + version, workflow + version, prompt, negative prompt, seed, parameters, reference/input assets, GPU, node, timestamp, duration (spec sections 46-47).
- **Approval status** (spec section 88): `PENDING → SELECTED | REJECTED`, then `FINAL`, `ARCHIVED` — for both images and videos.
- **History** (spec section 87): per shot, every generation is traceable with its status (failed/accepted/rejected) and metadata.
- **Preview/compare** (spec section 86): the UI previews images/videos/references and compares versions side by side.

## Prompt and style templates (spec sections 27-28)

- **Composable prompt system** (spec section 27): final prompt = base style + character + location + action + camera + lighting + mood + environment + visual style + technical parameters. The composer is a domain service; components are data.
- **Templates** (spec section 28): reusable, **versioned** templates for prompts, negative prompts, style, camera, and lighting. Templates are first-class entities with versions, owned by a user or global, and can be applied to shots or used as generation defaults.

## Tags (spec section 79)

- Free-form tags on projects, sequences, shots, assets, models, and generated media.
- No predefined genre vocabulary (spec section 79) — tags are user-defined data with GIN-indexed JSONB storage and a tag-completion API.

## Search (spec section 78)

- Global search across projects, characters, locations, assets, shots, jobs, models, workflows, logs — backed by Postgres full-text search over the metadata columns, with tag filters.

## Media pipeline (spec section 85)

```text
Project → Characters/Locations/Assets → Shot definition → Reference image
→ Image generation → Image selection → Image-to-video → Video selection
→ Upscaling → Post-processing → Final export
```

Every stage preserves metadata; the data model links each stage's outputs to its inputs, so the full provenance chain is queryable (spec sections 47, 90).
