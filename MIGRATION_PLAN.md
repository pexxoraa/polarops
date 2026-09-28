# PolarOps Incremental Migration Plan — Current

Date: 2026-09-28

## Phase 0 — Baseline and audit

Status: COMPLETE for current snapshot.

Verified:
- current source/module tree
- wrangler bindings
- remote D1 migrations and row counts
- FastAPI route ownership
- Durable Object/WebSocket gate
- frontend/offline architecture
- auth/authorization path
- backup coverage
- external integrations
- current syntax checks

Baseline result:
- Python source compiles
- public/static/app.js passes node --check
- live /api/health is healthy
- scripts/offline_checks.py is stale and fails because routes have moved out of worker.py

No production data was modified during this audit.

## Phase 1A — Security/integrity fixes

Next implementation, in this order:
1. validate every related location/personnel/vehicle/telemetry entity against the parent expedition
2. add tests proving cross-expedition related IDs are rejected
3. make missing AUTH_SECRET fail closed in authenticated production paths
4. add/refresh WebSocket tenant-isolation tests

Deploy and smoke-test before continuing.

## Phase 1B — Reliability foundation

1. repair baseline test harness so modular routes are discovered across src/
2. introduce processed mutation IDs server-side with a numbered migration
3. send mutation IDs for online and offline writes
4. move offline queue persistence to IndexedDB
5. preserve explicit PENDING/SYNCING/SYNCED/CONFLICT/FAILED states
6. add retry tests for timeout-after-commit and duplicate mutation replay

## Phase 1C — Concurrency / restore safety

1. add version + updated_at to conflict-sensitive records using numbered migrations
2. require expected_version on dangerous concurrent updates
3. return 409 on version mismatch
4. implement backup restore validator against an isolated/local database
5. test event-history round trip

## Phase 2 — Backend modular domains

Migrate one domain per commit/deployment.

Order:
1. auth
2. users/organizations
3. expeditions
4. locations
5. personnel
6. cargo
7. inventory
8. vehicles
9. assets
10. incidents
11. telemetry/dashboard
12. operations feed
13. activity/backups

For each domain:
- capture current route inputs/outputs and DB effects
- add/extend tests
- extract schemas
- extract repository SQL
- extract service workflow
- keep route compatibility
- run local checks
- deploy
- run production smoke checks

## Phase 3 — Thin worker entrypoint

After domain routes have moved:
- worker.py keeps only WorkerEntrypoint
- ASGI forwarding
- WebSocket pre-authorization
- Durable Object export/import
- environment handoff

app.py owns:
- FastAPI construction
- middleware
- exception handlers
- router registration

## Phase 4 — Realtime normalization

- event_id
- type
- expedition_id
- entity_type
- entity_id
- occurred_at
- data

D1 remains authoritative.
Broadcast only after successful persistence.
Add structured logging instead of silent exception swallowing.

## Phase 5 — Frontend modularization

Keep vanilla JavaScript.

Extract in this order:
1. core/state.js
2. core/api.js
3. core/cache.js
4. core/offline.js
5. core/auth.js
6. core/realtime.js
7. core/router.js
8. modal/toast/table/badge components
9. page modules

No framework migration.

## Phase 6 — Production hardening

- request IDs
- standard error envelope
- staging resources/environment
- CI checks
- security test suite
- offline test suite
- realtime test suite
- backup/restore test suite
- production smoke tests
- enable and bind R2 BACKUPS when the Cloudflare account has R2 enabled

## Gate for every phase

Do not move to the next phase unless:
- Python/JS syntax checks pass
- relevant tests pass
- migration is deterministic and safe
- local behavior is verified
- Cloudflare deploy succeeds
- production smoke checks pass
- D1 data remains intact
