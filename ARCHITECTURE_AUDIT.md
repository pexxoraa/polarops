# PolarOps Architecture Audit — Current Production Snapshot

Date: 2026-09-28
Branch: cloudflare-deploy
Audited HEAD before changes: 7aedff1
Scope: deployed Cloudflare application, current source tree, remote D1 schema/migrations, frontend/PWA, realtime and backup paths.

## Executive summary

PolarOps is a working Cloudflare-native modular-monolith migration in progress. The stack is appropriate and should be preserved.

The codebase is no longer the original fully monolithic baseline: environment/facility domains, core security/config/time, D1 helpers, selected repositories/services, realtime modules and app construction have already been extracted. However, the migration is only partial.

Current concentration remains significant:
- src/worker.py: 1,140 lines
- public/static/app.js: 815 lines

The most important current findings are:
1. WebSocket pre-authorization is materially improved and now checks ticket, user, expedition and organization before the Durable Object room is reached.
2. Offline replay now checks HTTP status, but the queue is still localStorage-based and there is no server-side idempotency or optimistic concurrency.
3. Critical cargo/inventory/incident multi-write workflows use D1 batch operations and broadcast after persistence.
4. Tenant checks exist for top-level expedition access, but several foreign-key inputs can still point at a location/personnel/vehicle belonging to another expedition. This is a cross-tenant integrity/read-leak risk and is now the highest unresolved security issue.
5. Backup event history is included, but restore validation does not exist and the production wrangler config has no BACKUPS R2 binding.
6. app.py exists, but worker.py still registers most domain routes directly on the imported FastAPI app.
7. facilities/environment are good examples of the target route -> service -> repository/integration pattern; most other domains remain in worker.py.
8. there is no real tests/ tree. scripts/offline_checks.py is stale and currently fails because it assumes every route remains in worker.py.

## 1. Current folder structure

Implemented modular areas:
- src/app.py
- src/core/{config,security,permissions,time}.py
- src/database/d1.py
- src/api/dependencies.py
- src/api/routes/{health,environment,facilities}.py
- src/repositories/{activity,data_sources,expeditions,external_cache,facilities,locations}.py
- src/services/{environment_service,facility_service}.py
- src/integrations/{comnap,open_meteo,parsing,polar_environment}.py
- src/realtime/{broadcaster,expedition_room}.py
- src/schemas/facility.py

Still monolithic:
- auth/users/organizations
- expeditions and most location mutations
- telemetry/dashboard
- personnel
- cargo
- inventory
- vehicles
- assets
- incidents
- operations feed
- activity
- backups
- static fallback routes

Frontend remains one large public/static/app.js plus CSS files.

## 2. Current Cloudflare bindings

wrangler.jsonc currently configures:
- ASSETS -> ./public
- DB -> polarops-db
- EXPEDITION_ROOM -> Durable Object class ExpeditionRoom
- COMNAP_FACILITIES_URL
- OPEN_METEO_URL
- OPERATIONS_FEED_URL

AUTH_SECRET exists as a Wrangler secret in production.

Important mismatch:
- backup code supports env.BACKUPS
- wrangler.jsonc does not define an R2 BACKUPS binding
- therefore R2 backup storage is currently not configured

No staging environment is defined.

## 3. FastAPI entrypoints

src/app.py creates the FastAPI instance and registers:
- health
- environment
- facilities

src/worker.py imports that app and then attaches the remaining production routes directly.

This is a transitional architecture. It works, but app construction/route ownership is split between app.py and worker.py.

## 4. Durable Object / WebSocket architecture

Current public WebSocket flow:
1. authenticated HTTP client requests /api/realtime/ticket
2. server verifies expedition access
3. server issues a short-lived expedition-specific ticket
4. Worker validates ticket purpose, expedition ID, user, active state and organization
5. only then is the request forwarded to EXPEDITION_ROOM
6. Durable Object accepts the socket and authenticates the normal session token

This closes the original direct cross-tenant room-entry gap.

Remaining work:
- automated security tests for cross-organization WebSocket denial
- standardized event envelope with event_id and occurred_at
- broadcaster currently swallows delivery exceptions without structured logging

## 5. D1 schema and production state

Remote migrations applied:
- 0001_initial.sql
- 0002_seed_demo.sql
- 0003_cloudflare_password_rounds.sql
- 0004_arctic_demo.sql
- 0005_environment_cache.sql
- 0006_research_station_reference.sql

Remote production snapshot during audit:
- organizations: 1
- users: 3
- expeditions: 2
- personnel: 12
- cargo: 11
- inventory items: 11
- vehicles: 9
- incidents: 0
- telemetry positions: 0
- public facilities: 114
- research station reference rows: 82

Missing for the requested production model:
- processed mutation/idempotency table
- version / updated_at conflict fields on conflict-sensitive records
- richer audit-event table
- restore metadata/versioning

## 6. Frontend architecture

public/static/app.js currently owns:
- authentication/session token
- API client
- cache
- offline queue
- realtime/WebSocket lifecycle
- router/navigation
- global state
- all page rendering
- modal/toast behavior
- Leaflet maps
- forms/mutations
- GPS/simulation helpers

It remains a frontend god module and should be split only after backend boundaries and tests are stable.

## 7. Offline architecture

Current strengths:
- GET cache fallback exists
- offline mutations are retained
- replay checks res.ok
- 401, 409, 429, 500+ and network failures are distinguished
- mutation status strings are already used during replay

Current risks:
- queue persistence is localStorage, not IndexedDB
- a fetch/network failure while navigator.onLine remains true is not queued
- mutation ID is added on replay, but normal online mutations do not consistently carry one
- server has no processed-mutation idempotency store
- no optimistic-concurrency version checks
- no conflict-resolution UI/workflow
- queue item bodies are untyped opaque JSON strings

## 8. Authentication architecture

Current:
- PBKDF2-SHA256
- HMAC-signed bearer token
- 12-hour expiry
- current_user dependency re-loads active user from D1
- short-lived realtime tickets

Risks / debt:
- token stored in localStorage, increasing impact of any XSS
- AUTH_SECRET helpers fall back to "dev-only-change-me" if binding is absent; production should fail closed
- login/user/password routes remain in worker.py
- auth repository/service boundary is not complete

Authentication behavior should otherwise be preserved during structural migration.

## 9. Authorization / tenant architecture

Good:
- organization comes from authenticated user, not browser
- ensure_expedition_access checks expedition.organization_id against user.organization_id
- most private endpoints call it
- facilities/environment routes have started using explicit permissions

Unresolved P0/P1 risk:
several mutation schemas accept related IDs without proving those related records belong to the same expedition:
- personnel.location_id
- cargo origin/destination/current location IDs
- inventory.location_id
- vehicle.location_id
- asset.location_id and assigned_to_personnel_id
- incident.location_id / assigned vehicle flows
- telemetry entity_id

A tenant can potentially submit an ID from another expedition. Joins may then expose names/metadata across expedition boundaries or create logically corrupt relationships.

This must be fixed before continuing cosmetic modularization.

## 10. Backup architecture

Current backup includes tenant-scoped:
- organizations
- users
- expeditions
- locations
- personnel
- cargo + cargo_events
- inventory_items + inventory_events
- vehicles
- assets
- incidents + incident_events
- activity
- telemetry_positions

It also includes selected public reference tables.

Problems:
- no restore endpoint/tool validation
- no restore integration tests
- R2 BACKUPS binding absent from production config
- research_station_reference/external_cache are not part of the backup payload; this is acceptable if treated as reproducible reference/cache data, but should be documented explicitly
- backup code remains in worker.py

## 11. External integrations

Already extracted:
- COMNAP
- Open-Meteo
- NOAA/NSIDC
- NOAA SWPC
- USGS polar seismic

Still in worker.py:
- authorized operations/worker feed

Facility integration demonstrates the desired normalization boundary using a schema.

## 12. Major architectural bugs / reliability issues

Highest unresolved:
1. cross-expedition related-ID validation gaps
2. no server idempotency for retries
3. no optimistic concurrency
4. no restore validation
5. no automated security/offline/realtime test suite
6. stale baseline test script
7. AUTH_SECRET fallback should fail closed
8. operations feed remains mixed into worker.py
9. most routes still own SQL/business logic directly
10. broadcaster silently swallows all realtime exceptions

## 13. Security risks

P0/P1:
- cross-expedition foreign reference injection
- lack of cross-tenant automated tests
- fail-open development AUTH_SECRET fallback
- browser bearer token in localStorage
- device identity/authorization for telemetry is coarse: an authenticated user with expedition access can submit position records for arbitrary entity IDs unless entity ownership is validated

## 14. Data-integrity risks

- no version fields / expected_version checks
- no mutation idempotency
- some mutations still write entity state and activity in separate operations
- incident code generation uses COUNT()+1 and can race
- generic patch_row is still in worker.py
- foreign-key relationships do not encode same-expedition ownership at the schema level

## 15. Technical debt

- worker.py still 1,140 lines
- app.js still 815 lines
- app.py route registration is incomplete
- no tests/ directory
- stale scripts/offline_checks.py assumptions
- no standard error envelope/request IDs
- no staging Cloudflare environment/resources
- untracked generated screenshots/runtime artifacts in the working tree
- documentation still contains some statements from earlier architecture states and needs synchronization

## 16. Refactoring order from this snapshot

P0 security first:
1. same-expedition validation for all related resource IDs and telemetry entities
2. fail closed when AUTH_SECRET is absent
3. add security tests for tenant and WebSocket isolation

P1 reliability:
4. baseline test harness and repair stale checks
5. server-side mutation idempotency
6. IndexedDB offline queue
7. optimistic concurrency/version fields
8. backup restore validation

P2 backend boundaries:
9. auth/users/organizations
10. expeditions/locations
11. personnel
12. cargo
13. inventory
14. vehicles/assets
15. incidents
16. telemetry/dashboard
17. operations feed
18. activity/backups/static routing

P3:
19. standard errors/request IDs/logging
20. frontend ES-module extraction
21. environment isolation/staging
22. CI smoke/security/offline/realtime tests

The application must remain deployable and the existing D1 database must remain usable after every step.
