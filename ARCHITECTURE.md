# PolarOps Architecture

PolarOps is a single Cloudflare application for Arctic and Antarctic expedition operations. The architecture is a **modular monolith**: one deployment, one FastAPI backend, one D1 operational database, Durable Objects for realtime coordination, Workers Static Assets for the PWA, and optional R2 for backups/files.

The design goal is not to split PolarOps into microservices. The goal is to make the existing application predictable, recoverable, testable, tenant-safe, and safe to change without replacing the working stack.

## Runtime architecture

```text
Browser / PWA
    |
    | HTTPS + JSON
    v
Cloudflare Worker entrypoint
    |
    +--> Workers Static Assets
    |
    +--> FastAPI application
    |       |
    |       +--> authentication / authorization
    |       +--> domain services
    |       +--> repositories
    |       +--> Cloudflare D1
    |       +--> external integrations
    |
    +--> WebSocket pre-authorization
            |
            +--> Expedition Durable Object
                    |
                    +--> hibernatable WebSocket clients
```

**D1 is the system of record.** Durable Objects coordinate realtime connections and fanout; they do not own persistent expedition state.

## Responsibility model

Normal API operations follow:

```text
HTTP request
    |
FastAPI route
    |
authentication / authorization
    |
service
    |
repository
    |
Cloudflare D1
    |
service response
```

Mutations that notify live clients follow:

```text
Browser
    |
API route
    |
service
    |
atomic D1 mutation
    |
commit succeeds
    |
realtime broadcaster
    |
Expedition Durable Object
    |
WebSocket clients
```

Responsibilities are intentionally separated:

- **Routes**: HTTP input/output and dependency wiring.
- **Schemas**: validation and serialization.
- **Services**: business rules and multi-step workflows.
- **Repositories**: D1 access and parameterized SQL.
- **Integrations**: COMNAP, Open-Meteo, NOAA/NSIDC, NOAA SWPC, USGS, and authorized operations feeds.
- **Realtime**: event envelopes, Durable Objects, and WebSocket communication.
- **Core**: security, permissions, middleware, configuration, errors, and time helpers.
- **Storage**: backups, exports, and future large-file handling.

## Target backend layout

```text
src/
  worker.py
  app.py

  core/
    config.py
    security.py
    permissions.py
    middleware.py
    exceptions.py
    time.py

  database/
    d1.py
    queries.py

  api/
    dependencies.py
    routes/
      auth.py
      users.py
      organizations.py
      expeditions.py
      locations.py
      personnel.py
      cargo.py
      inventory.py
      vehicles.py
      assets.py
      incidents.py
      telemetry.py
      facilities.py
      integrations.py
      activity.py
      backups.py
      health.py

  schemas/
  repositories/
  services/

  integrations/
    comnap.py
    open_meteo.py
    operations_feed.py

  realtime/
    broadcaster.py
    expedition_room.py

  storage/
    backups.py
```

Files are created only when they have a clear responsibility. PolarOps does not introduce microservices, Redis, Kafka, GraphQL, React, Next.js, or an ORM simply for architectural appearance.

## Cloudflare bindings

Current production bindings:

- `ASSETS` - Workers Static Assets.
- `DB` - Cloudflare D1 database `polarops-db`.
- `EXPEDITION_ROOM` - Durable Object namespace for expedition realtime rooms.
- `AUTH_SECRET` - Worker secret used to sign authenticated sessions and realtime tickets.
- `COMNAP_FACILITIES_URL` - official COMNAP facilities dataset.
- `OPEN_METEO_URL` - current model weather endpoint.
- `OPERATIONS_FEED_URL` - optional operator-controlled personnel/location feed.

R2 backup storage is supported by the application design, but the current deployment does **not** have a `BACKUPS` R2 binding configured. The backup API therefore reports that R2 is not configured instead of silently claiming a cloud backup was stored.

## Tenant boundary

Every private expedition belongs to an `organization_id`. Every authenticated user also belongs to an organization.

The invariant is:

```text
resource.organization_id
==
authenticated_user.organization_id
```

For resources that do not carry `organization_id` directly, ownership is derived through their parent expedition.

The browser is never trusted to choose the tenant. Tenant context is derived from authenticated server-side identity.

## WebSocket security

A valid login token alone is not enough to join an expedition realtime room.

Connection flow:

```text
Browser
    |
GET /api/realtime/ticket?expedition_id=...
    |
authenticate user
    |
load expedition
    |
verify expedition.organization_id == user.organization_id
    |
issue short-lived signed realtime ticket
    |
wss://.../ws/expeditions/{id}?ticket=...
    |
Worker validates ticket + user + expedition again
    |
Durable Object room
    |
WebSocket accepted
```

The Durable Object still performs session authentication after upgrade, but cross-tenant access is blocked before the request reaches the room.

## Realtime model

Realtime messages notify clients that persistent state changed; they are not authoritative state.

Typical event types include:

- `personnel.checkin`
- `telemetry.updated`
- `cargo.moved`
- `inventory.adjusted`
- `vehicle.updated`
- `incident.created`
- `incident.dispatched`
- `incident.resolved`

Clients must remain capable of re-fetching authoritative state from the API/D1.

## D1 data model

Operational tables include:

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

Public/reference tables include:

- public_facilities
- facility_weather
- data_sources
- external_cache

Telemetry is append-oriented. Current position is derived from the newest valid telemetry record instead of overwriting history.

## Transactional operations

Related writes that represent one business action are executed with D1 batch semantics where practical.

Examples:

- cargo registration -> cargo row + cargo event + activity event
- cargo movement -> cargo update + movement event + activity event
- inventory adjustment -> quantity update + inventory event + activity event
- incident creation -> incident + incident event + activity event
- incident dispatch/resolve -> incident update + incident event + activity event

Realtime broadcast happens only after the persistent database operation succeeds.

## Frontend architecture

Current frontend remains vanilla JavaScript and a PWA. The target module layout is:

```text
public/
  index.html
  manifest.webmanifest
  service-worker.js

  static/
    css/
      base.css
      components.css
      pages.css

    js/
      app.js

      core/
        api.js
        auth.js
        state.js
        cache.js
        offline.js
        realtime.js
        router.js

      components/
        modal.js
        toast.js
        table.js
        badges.js

      pages/
        overview.js
        personnel.js
        cargo.js
        inventory.js
        assets.js
        vehicles.js
        emergency.js
        environment.js
        network.js
        activity.js
        settings.js
```

The migration is incremental. The deployed `public/static/app.js` continues to work while responsibilities are extracted page by page.

## Offline-first behavior

PolarOps assumes unreliable field connectivity.

Current behavior:

- read snapshots can fall back to cached data;
- offline writes are queued;
- replay now checks HTTP status rather than treating every `fetch()` response as success;
- rejected/conflicting mutations remain queued instead of being silently discarded.

Target behavior:

```text
User action
    |
    +-- online --> API --> D1
    |
    +-- offline --> IndexedDB mutation queue
                       |
                       +--> PENDING / SYNCING / SYNCED / CONFLICT / FAILED
                       |
                    reconnect
                       |
                    sync engine
                       |
                    API
                       |
                    idempotency + version checks
                       |
                    D1
```

The next reliability phase adds server-side mutation IDs, IndexedDB persistence, and optimistic concurrency.

## External polar data

Public science/reference information is deliberately separated from private operational data.

Antarctic public infrastructure:

- **COMNAP Antarctic Facilities List (November 2024)** is the authoritative public facility snapshot used by PolarOps.
- The synchronized dataset contains 114 COMNAP records supplied by national Antarctic programs.
- One source record, Zhongshan Skiway, carries a positive latitude in the November 2024 CSV; PolarOps preserves the source value and does not silently invent a correction.

Environmental/science context:

- Open-Meteo - model current conditions at mapped coordinates.
- NOAA/NSIDC - daily Arctic and Antarctic sea-ice products.
- NOAA SWPC - geomagnetic Kp and auroral products.
- USGS - recent polar seismic events.
- INTERACT/SAON - Arctic research and observing discovery resources.
- BAS/SCAR and AntarcticGlaciers.org - Antarctic mapping and science references.

Private personnel, vehicles, cargo, inventory, incidents, and live positions are not scraped from public websites. They come from authenticated users, devices, or an authorized organization feed.

## Arctic and Antarctic separation

PolarOps treats the two polar regions as separate operational contexts.

**Antarctic / South**
- COMNAP public facility network
- Antarctic expedition locations
- Antarctic sea-ice/environment products
- private mission operations

**Arctic / North**
- Arctic expedition locations
- Arctic science/observing resources
- Arctic sea-ice/environment products
- private mission operations

Antarctic COMNAP facilities are not presented as Arctic infrastructure.

## Backups

Commander backups are tenant-scoped and include current operational state plus event history:

- organizations
- users
- expeditions
- locations
- personnel
- cargo
- cargo_events
- inventory_items
- inventory_events
- vehicles
- assets
- incidents
- incident_events
- activity
- telemetry_positions

Public facility/reference tables are included as reference context.

R2 is intended for durable backup/export objects once the `BACKUPS` bucket binding is enabled. Restore validation remains a required production-hardening milestone.

## Migration status

**Phase 0 - Baseline: complete**
- architecture audit
- D1/binding/API inventory
- migration plan

**Phase 1 - Critical security/reliability: complete and deployed in 2.1.0-cloudflare**
- WebSocket tenant pre-authorization with short-lived ticket
- HTTP-aware offline replay
- D1 batch operations for critical cargo/inventory/incident workflows
- event-history backup coverage
- explicit R2 backup status

**Phase 2 - Backend modular boundaries: started**
- extracted `core/config.py`
- extracted `core/security.py`
- extracted `core/time.py`
- extracted `database/d1.py`
- deployed and browser-smoke-tested the extracted modules

**Next**
- extract realtime and integration modules
- migrate domain routes to service/repository layers one domain at a time
- split the frontend into ES modules
- move offline queue to IndexedDB
- add idempotency/version conflict protection
- add security/offline/realtime/backup test suites

See `ARCHITECTURE_AUDIT.md` and `MIGRATION_PLAN.md` for the detailed audit and sequence.
