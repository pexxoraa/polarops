# PolarOps Incremental Migration Plan

This migration follows a strict no-big-bang approach.

## Phase 0 - Baseline and audit

Status: COMPLETE

- capture current folder structure
- record bindings and D1 schema
- identify API routes
- document Durable Object behavior
- document frontend/offline/auth/backup architecture
- identify P0/P1 risks

## Phase 1 - Critical security and reliability

Status: IN PROGRESS

1. Authorize WebSocket expedition access before Durable Object room entry.
2. Preserve rejected offline mutations instead of deleting them.
3. Include cargo/inventory/incident event history in backups.
4. Make multi-write operational mutations atomic with D1 batch.
5. Stop silently presenting R2 backup as successful when the binding is unavailable.

## Phase 2 - Backend modular boundaries

Extract without breaking routes:

- `src/core/`
- `src/database/`
- `src/realtime/`
- `src/integrations/`
- `src/schemas/`
- `src/api/dependencies.py`

Then migrate domains one at a time to:

route -> service -> repository -> D1

Order:

1. auth
2. expeditions/locations
3. personnel
4. cargo
5. inventory
6. vehicles/assets
7. incidents
8. telemetry
9. facilities/integrations
10. activity/backups/health

## Phase 3 - Thin Cloudflare entrypoint

Move FastAPI application construction to `src/app.py`.

Reduce `src/worker.py` to:

- Cloudflare Worker entrypoint
- WebSocket pre-authorization
- Durable Object export
- ASGI handoff

## Phase 4 - Frontend modularization

Split `public/static/app.js` into ES modules:

- `core/api.js`
- `core/auth.js`
- `core/state.js`
- `core/cache.js`
- `core/offline.js`
- `core/realtime.js`
- `core/router.js`
- reusable components
- page modules

No framework migration.

## Phase 5 - Offline correctness

- IndexedDB mutation queue
- mutation IDs
- states: PENDING/SYNCING/SYNCED/CONFLICT/FAILED
- safe retry policy
- 409 conflict handling
- server-side idempotency

## Phase 6 - Concurrency/data integrity

- version + updated_at on conflict-sensitive records
- expected_version update semantics
- restore validation for backups
- telemetry retention policy

## Phase 7 - Production hardening

- structured request IDs
- standardized error envelope
- environment separation
- security/offline/realtime/backup tests
- CI production smoke tests

Each phase requires syntax checks, migrations where needed, deployment, and production smoke validation before the next phase.
