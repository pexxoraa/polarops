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

Status: COMPLETE - deployed as PolarOps 2.1.0-cloudflare

1. WebSocket expedition access is pre-authorized with a short-lived server-issued ticket before Durable Object room entry.
2. Rejected offline mutations remain queued with PENDING / FAILED / CONFLICT state instead of being silently deleted.
3. Backups include cargo, inventory and incident event history.
4. Critical cargo, inventory and incident multi-write workflows use D1 batch operations and broadcast only after persistence succeeds.
5. Missing R2 backup configuration is reported explicitly instead of being silently swallowed.

## Phase 2 - Backend modular boundaries

Status: STARTED - core configuration, security/time helpers and D1 access are now extracted from `worker.py`.

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
