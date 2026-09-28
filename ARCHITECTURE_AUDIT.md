# PolarOps Architecture Audit

Date: 2026-09-28
Branch: cloudflare-deploy
Baseline commit: 580af998f482c9c7af9bb5a5ca019c35c2e31bf0

## Executive summary

PolarOps is already deployed and functional on Cloudflare, but backend and frontend responsibilities are concentrated in two large modules:

- `src/worker.py`: 1,847 lines
- `public/static/app.js`: 780 lines

The current stack is appropriate and should be preserved: Cloudflare Python Workers + FastAPI + D1 + Durable Objects + WebSockets + Workers Static Assets + vanilla JavaScript/PWA.

The target is a modular monolith, not microservices.

## 1. Current folder structure

Relevant runtime files:

- `src/worker.py` - Cloudflare entrypoint, FastAPI app, routes, schemas, auth, SQL, external integrations, realtime, Durable Object
- `public/index.html`
- `public/static/app.js`
- `public/static/app.css`
- `public/static/reference-ui.css`
- `public/service-worker.js`
- `migrations/0001_initial.sql` through `0005_environment_cache.sql`
- `wrangler.jsonc`

## 2. Cloudflare bindings

Configured:

- `ASSETS` - Workers Static Assets
- `DB` - Cloudflare D1
- `EXPEDITION_ROOM` - Durable Object
- `AUTH_SECRET` - secret created with Wrangler
- `COMNAP_FACILITIES_URL`
- `OPEN_METEO_URL`
- `OPERATIONS_FEED_URL`

Important mismatch:

- backup code references `env.BACKUPS`, but `wrangler.jsonc` currently has no R2 `BACKUPS` binding.
- the backup handler silently ignores that failure.

## 3. FastAPI entrypoint

`src/worker.py` creates the FastAPI application directly and also contains the Cloudflare Worker entrypoint.

There are roughly 45+ API/static routes covering:

- auth/users/organizations
- expeditions/locations
- telemetry/environment/dashboard
- personnel/cargo/inventory
- vehicles/assets/incidents
- public facilities/integrations
- activity/backups/health/static frontend

## 4. Durable Object architecture

`ExpeditionRoom` is a hibernatable WebSocket room keyed by expedition ID.

D1 is correctly treated as the system of record and Durable Objects are used for realtime fanout.

Critical security issue:

The Worker currently forwards `/ws/expeditions/{expedition_id}` to the Durable Object before tenant authorization. The Durable Object accepts any valid PolarOps token after connection and does not verify that the requested expedition belongs to the authenticated organization.

This is a cross-tenant WebSocket risk and is P0.

## 5. D1 schema

D1 contains:

- organizations
- users
- expeditions
- locations
- personnel
- cargo / cargo_events
- inventory_items / inventory_events
- vehicles
- assets
- incidents / incident_events
- activity
- telemetry_positions
- public_facilities
- facility_weather
- data_sources
- external_cache

Operational ownership is primarily inherited through `expedition_id -> expeditions.organization_id`.

## 6. Frontend architecture

`public/static/app.js` currently combines:

- authentication/session handling
- API client
- caching
- offline queue
- WebSocket lifecycle
- navigation/router
- global state
- modal/toast components
- all page rendering
- Leaflet maps
- external-data UI
- form submission workflows

The file is functional but is a frontend god module.

## 7. Offline architecture

Current queue:

- stored in `localStorage`
- queues writes only when browser reports offline
- replay uses raw `fetch()`
- replay does not verify HTTP response status before deleting a queued mutation
- no mutation status model
- no idempotency key
- no optimistic concurrency/version checking

Critical reliability issue:

A replayed request that returns HTTP 4xx/5xx can be removed from the queue even though the server rejected it.

## 8. Authentication architecture

Authentication currently uses:

- PBKDF2-SHA256 password hashes using Workers Web Crypto
- signed bearer tokens
- 12 hour token expiry
- centralized `current_user()` dependency

This should be preserved during the first structural refactor.

## 9. Authorization architecture

Most HTTP entity routes correctly resolve the resource's expedition and call `ensure_expedition_access()`.

Weaknesses:

- role checks are scattered through `require("commander", ...)`
- no explicit permission registry
- WebSocket path bypasses expedition tenant authorization before Durable Object connection

## 10. Backup architecture

Current backup endpoint exports most current-state tables and telemetry.

Problems:

- `cargo_events`, `inventory_events`, and `incident_events` are declared in a table list but are not actually included in the tenant backup payload
- restore validation does not exist
- R2 storage silently fails because the current deployment has no `BACKUPS` binding

## 11. External integrations

Current integration logic is inside `worker.py`:

- COMNAP facilities
- Open-Meteo
- NOAA/NSIDC sea ice
- NOAA SWPC space weather
- USGS seismic feed
- authorized operations feed

External responses require normalization and validation boundaries outside HTTP routes.

## 12. Major architectural bugs

P0/P1 findings:

1. Cross-tenant WebSocket authorization gap.
2. Offline replay treats network success as business success and ignores HTTP status.
3. Backup omits operational event history.
4. R2 backup configuration mismatch is swallowed.
5. Multi-write workflows can partially commit.
6. SQL and business logic are embedded in routes.
7. Realtime event envelopes are inconsistent with an explicit event ID / occurred_at model.
8. Frontend state, API, routing and page rendering are coupled.

## 13. Security risks

- WebSocket room authorization is the highest priority.
- Role checks are string-based and scattered.
- Tenant authorization depends on every route remembering to call the correct helper.
- telemetry writes are authenticated and expedition-scoped, but device/personnel ownership is not yet modeled.
- demo credentials must not be retained for a real shared deployment.

## 14. Data-integrity risks

- cargo movement, inventory adjustment and incident creation each perform related writes separately
- no idempotency handling for offline retries
- no optimistic concurrency/version fields
- backup/restore has no tested restore path
- external feed failures are partially normalized but not consistently schema-validated

## 15. Technical debt

- backend god module
- frontend god module
- no clean repositories/services boundary
- no explicit domain permissions
- no request ID/error envelope
- no structured offline mutation state
- no architecture-level test suite split by unit/integration/security/offline/realtime

## 16. Refactoring order

1. P0 WebSocket tenant authorization
2. Offline replay correctness
3. Backup history coverage
4. Atomic multi-write operations
5. Core/database/security/realtime module extraction
6. Auth + expedition repository/service/routes
7. Personnel/cargo/inventory modules
8. Vehicle/asset/incident/telemetry modules
9. Integration modules
10. Frontend core extraction
11. IndexedDB/idempotency/versioning
12. production hardening and test expansion

The application must remain deployable and the existing D1 database must remain usable after every phase.
