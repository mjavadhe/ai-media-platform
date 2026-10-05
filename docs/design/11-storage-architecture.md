# 11 — Storage Architecture

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md)

## Principles (spec sections 43-45, 76)

- The relational database stores **metadata only** — never large media (spec section 100, rule 11).
- All media access goes through a **storage abstraction layer**; providers are interchangeable (spec sections 44, 76).
- No hard-coded storage paths (spec section 100, rule 5): bucket/prefix layout is configuration.

## Storage port

```text
StorageBackend (interface)
  ├── put(key, file, metadata) -> StorageObject
  ├── get(key) -> stream
  ├── delete(key)
  ├── exists(key) -> bool
  └── url(key, expiry) -> str (time-limited, for previews)

  ├── LocalStorage      (default; Phase 1)
  ├── S3Storage         (MinIO / S3 — spec §44)
  └── NfsSmbStorage     (later)
```

## Object layout (spec section 45 — example, configured not hard-coded)

```text
storage/
  projects/<project_id>/{assets,characters,locations,sequences,shots,images,videos,exports}/
  shared/{characters,locations,assets}/
  models/  workflows/  logs/
```

The layout is a configurable prefix template; the DB records the resolved key per object.

## Metadata

`storage_objects` table: `id, backend, bucket, key, size, mime_type, checksum, owner_type, owner_id, created_at`. Checksums enable dedupe and integrity verification (spec section 75).

## Scalability and failure handling (spec sections 75-76)

- Uploads: resumable where the backend supports it; retries with backoff.
- Downloads: time-limited signed URLs for previews (spec section 86) — the UI never touches storage directly.
- Provider failure: jobs fail with a classified storage error and retry per policy (spec section 93).
- Backup: policies for DB, project metadata, workflow definitions, model registry, and important assets (spec section 77) — Phase 10.

## Mirrors and offline operation (hardware.md section C)

Model downloads and container pulls support configurable mirrors and pre-seeded offline paths; the storage layer is agnostic to where bytes physically live.
