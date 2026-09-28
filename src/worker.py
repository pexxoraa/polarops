from __future__ import annotations

import json
import math
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, Field
from workers import Response, WorkerEntrypoint, asgi, fetch as cf_fetch

from api.dependencies import current_user, ensure_expedition_access, require
from app import app
from core.config import APP_VERSION, env_value, required_env_value
from core.security import (
    decode_ws_ticket,
    hash_password,
    make_token,
    make_ws_ticket,
    verify_password,
)
from core.time import utcnow
from database.d1 import (
    last_row_id,
    q_all,
    q_batch,
    q_first,
    q_value,
    q_write,
    q_write_batch,
    to_py,
)
from integrations.parsing import parse_coord
from realtime.broadcaster import broadcast
from realtime.expedition_room import ExpeditionRoom
from repositories.activity import log_activity
from services.relationship_service import valid_location_ids, valid_personnel_ids
from services.realtime_access_service import realtime_ticket_allows_expedition


# ----------------------------- Generic helpers -----------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def latest_telemetry(env, expedition_id: int, entity_type: str | None = None) -> list[dict]:
    clause = " AND entity_type=?" if entity_type else ""
    params = [expedition_id] + ([entity_type] if entity_type else [])
    return await q_all(env, f"""
        SELECT t.* FROM telemetry_positions t
        JOIN (
          SELECT entity_type,entity_id,MAX(id) max_id
          FROM telemetry_positions
          WHERE expedition_id=?{clause}
          GROUP BY entity_type,entity_id
        ) latest ON latest.max_id=t.id
        ORDER BY t.entity_type,t.entity_id
    """, *params)


def apply_live_positions(rows: list[dict], positions: list[dict]) -> list[dict]:
    live = {int(p["entity_id"]): p for p in positions}
    for row in rows:
        pos = live.get(int(row["id"]))
        row["live_latitude"] = pos.get("latitude") if pos else None
        row["live_longitude"] = pos.get("longitude") if pos else None
        row["telemetry_recorded_at"] = pos.get("recorded_at") if pos else None
        row["telemetry_source"] = pos.get("source") if pos else None
        row["accuracy_m"] = pos.get("accuracy_m") if pos else None
        row["speed_kph"] = pos.get("speed_kph") if pos else None
        row["heading"] = pos.get("heading") if pos else None
        if pos:
            row["latitude"] = pos.get("latitude")
            row["longitude"] = pos.get("longitude")
    return rows


async def patch_row(env, table: str, item_id: int, values: dict, allowed: set[str]):
    data = {k: v for k, v in values.items() if v is not None and k in allowed}
    if not data:
        return
    assignments = ",".join(f"{k}=?" for k in data)
    await q_write(env, f"UPDATE {table} SET {assignments} WHERE id=?", *data.values(), item_id)


# ----------------------------- Request models ------------------------------

class LoginIn(BaseModel):
    email: str
    password: str

class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)

class UserCreate(BaseModel):
    email: str
    name: str
    role: str
    password: str = Field(min_length=8)

class ExpeditionIn(BaseModel):
    name: str
    region: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str = "Planning"
    description: str = ""

class LocationIn(BaseModel):
    expedition_id: int
    name: str
    type: str = "Camp"
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class PersonnelIn(BaseModel):
    expedition_id: int
    name: str
    role: str
    team: str = ""
    location_id: Optional[int] = None
    status: str = "Safe"
    contact: str = ""
    clearance_status: str = "Cleared"

class PersonnelPatch(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    team: Optional[str] = None
    location_id: Optional[int] = None
    status: Optional[str] = None
    contact: Optional[str] = None
    clearance_status: Optional[str] = None

class CheckinIn(BaseModel):
    location_id: Optional[int] = None
    status: str = "Safe"

class CargoIn(BaseModel):
    expedition_id: int
    code: str
    name: str
    priority: str = "Medium"
    origin_location_id: Optional[int] = None
    destination_location_id: Optional[int] = None
    current_location_id: Optional[int] = None
    status: str = "Registered"
    quantity: float = 1
    unit: str = "unit"
    assigned_to: str = ""

class CargoPatch(BaseModel):
    name: Optional[str] = None
    priority: Optional[str] = None
    destination_location_id: Optional[int] = None
    assigned_to: Optional[str] = None

class CargoMove(BaseModel):
    location_id: int
    status: str = "In Transit"
    note: str = ""

class InventoryIn(BaseModel):
    expedition_id: int
    sku: str
    name: str
    location_id: Optional[int] = None
    quantity: float = 0
    min_quantity: float = 0
    unit: str = "units"
    expiry_date: Optional[str] = None

class InventoryPatch(BaseModel):
    name: Optional[str] = None
    location_id: Optional[int] = None
    min_quantity: Optional[float] = None
    unit: Optional[str] = None
    expiry_date: Optional[str] = None

class InventoryAdjust(BaseModel):
    delta: float
    reason: str

class VehicleIn(BaseModel):
    expedition_id: int
    code: str
    name: str
    type: str = "Ground"
    location_id: Optional[int] = None
    status: str = "Operational"
    fuel_percent: float = 100
    range_km: float = 0

class VehiclePatch(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    location_id: Optional[int] = None
    status: Optional[str] = None
    fuel_percent: Optional[float] = None
    range_km: Optional[float] = None

class AssetIn(BaseModel):
    expedition_id: int
    code: str
    name: str
    category: str = "Equipment"
    location_id: Optional[int] = None
    status: str = "Available"
    serial_number: str = ""
    assigned_to_personnel_id: Optional[int] = None

class AssetPatch(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    location_id: Optional[int] = None
    status: Optional[str] = None
    serial_number: Optional[str] = None
    assigned_to_personnel_id: Optional[int] = None

class IncidentIn(BaseModel):
    expedition_id: int
    title: str = "Field Emergency"
    type: str = "Field Emergency"
    severity: str = "High"
    location_id: Optional[int] = None
    description: str = ""
    affected_count: int = 0

class IncidentEventIn(BaseModel):
    event_type: str
    note: str

class TelemetryPositionIn(BaseModel):
    expedition_id: int
    entity_type: str
    entity_id: int
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: Optional[float] = None
    accuracy_m: Optional[float] = Field(default=None, ge=0)
    speed_kph: Optional[float] = Field(default=None, ge=0)
    heading: Optional[float] = Field(default=None, ge=0, le=360)
    fuel_percent: Optional[float] = Field(default=None, ge=0, le=100)
    source: str = "gps"
    recorded_at: Optional[str] = None

class TelemetryBatchIn(BaseModel):
    positions: list[TelemetryPositionIn] = Field(min_length=1, max_length=500)


# ----------------------------- Middleware ---------------------------------

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(self), camera=(self)"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https://server.arcgisonline.com https://noaadata.apps.nsidc.org https://services.swpc.noaa.gov; media-src 'self' blob:; connect-src 'self' ws: wss:; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ----------------------------- Auth / tenant -------------------------------

@app.post("/api/auth/login")
async def login(data: LoginIn, request: Request):
    env = request.scope["env"]
    row = await q_first(env, "SELECT * FROM users WHERE lower(email)=lower(?)", data.email.strip())
    if not row or not row.get("active") or not await verify_password(data.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = make_token(row, required_env_value(env, "AUTH_SECRET"))
    return {"token": token, "user": {k: row[k] for k in ("id", "organization_id", "email", "name", "role")}}


@app.get("/api/me")
async def me(user=Depends(current_user)):
    return user


@app.get("/api/bootstrap")
async def bootstrap(request: Request, expedition_id: int | None = None, user=Depends(current_user)):
    env = request.scope["env"]
    expeditions_rows = await q_all(
        env,
        "SELECT * FROM expeditions WHERE organization_id=? ORDER BY id DESC",
        user["organization_id"],
    )
    if not expeditions_rows:
        return {"user": user, "expeditions": [], "expedition_id": None, "dashboard": None}
    allowed_ids = {int(row["id"]) for row in expeditions_rows}
    selected_id = int(expedition_id) if expedition_id is not None and int(expedition_id) in allowed_ids else int(expeditions_rows[0]["id"])
    dashboard_data = await dashboard(selected_id, request, user)
    return {
        "user": user,
        "expeditions": expeditions_rows,
        "expedition_id": selected_id,
        "dashboard": dashboard_data,
    }


@app.get("/api/realtime/ticket")
async def realtime_ticket(expedition_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    secret = required_env_value(env, "AUTH_SECRET")
    return {
        "ticket": make_ws_ticket(user, expedition_id, secret, 60),
        "expedition_id": expedition_id,
        "expires_in": 60,
    }


@app.post("/api/me/password")
async def change_password(data: PasswordChange, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    row = await q_first(env, "SELECT password_hash FROM users WHERE id=?", user["id"])
    if not row or not await verify_password(data.current_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    await q_write(env, "UPDATE users SET password_hash=? WHERE id=?", await hash_password(data.new_password), user["id"])
    return {"ok": True}


@app.get("/api/users")
async def list_users(request: Request, user=Depends(require("commander"))):
    return await q_all(
        request.scope["env"],
        "SELECT id,organization_id,email,name,role,active,created_at FROM users WHERE organization_id=? ORDER BY name",
        user["organization_id"],
    )


@app.post("/api/users")
async def add_user(data: UserCreate, request: Request, user=Depends(require("commander"))):
    if data.role not in ("commander", "logistics", "field"):
        raise HTTPException(400, "Invalid role")
    env = request.scope["env"]
    try:
        result = await q_write(
            env,
            "INSERT INTO users(organization_id,email,name,role,password_hash,active,created_at) VALUES(?,?,?,?,?,1,?)",
            user["organization_id"], data.email.strip().lower(), data.name, data.role, await hash_password(data.password), utcnow(),
        )
    except Exception:
        raise HTTPException(409, "Email already exists")
    return {"id": last_row_id(result)}


@app.get("/api/organizations")
async def organization(request: Request, user=Depends(current_user)):
    row = await q_first(request.scope["env"], "SELECT * FROM organizations WHERE id=?", user["organization_id"])
    return [row] if row else []


# ----------------------------- Expeditions/locations ----------------------

@app.get("/api/expeditions")
async def expeditions(request: Request, user=Depends(current_user)):
    return await q_all(
        request.scope["env"],
        "SELECT * FROM expeditions WHERE organization_id=? ORDER BY id DESC",
        user["organization_id"],
    )


@app.post("/api/expeditions")
async def add_expedition(data: ExpeditionIn, request: Request, user=Depends(require("commander"))):
    env = request.scope["env"]
    r = await q_write(
        env,
        "INSERT INTO expeditions(organization_id,name,region,start_date,end_date,status,description,created_at) VALUES(?,?,?,?,?,?,?,?)",
        user["organization_id"], data.name, data.region, data.start_date, data.end_date, data.status, data.description, utcnow(),
    )
    expedition_id = last_row_id(r)
    ts = utcnow()
    defaults = [
        ("Personnel", "Personnel roster and field-team assignments confirmed", "Expedition Lead"),
        ("Medical", "Medical kits and evacuation plan checked", "Medical Officer"),
        ("Communications", "Primary and backup communications tested", "Communications"),
        ("Vehicles", "Vehicle serviceability and recovery equipment checked", "Logistics"),
        ("Fuel", "Fuel reserve verified against planned traverse", "Logistics"),
        ("Food", "Food and field rations checked against team duration", "Logistics"),
        ("Emergency", "Emergency shelters, beacons and recovery equipment checked", "Expedition Lead"),
        ("Permits", "Required permits and operating documents reviewed", "Expedition Lead"),
        ("Weather", "Forecast and operating thresholds reviewed", "Expedition Lead"),
        ("Route", "Route, alternates and check-in points reviewed", "Field Team Lead"),
    ]
    await q_write_batch(env, [(
        "INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (expedition_id, category, label, "Pending", owner, ts, ts),
    ) for category, label, owner in defaults])
    return {"id": expedition_id}


@app.patch("/api/expeditions/{item_id}")
async def edit_expedition(item_id: int, data: ExpeditionIn, request: Request, user=Depends(require("commander"))):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, item_id)
    await q_write(
        env,
        "UPDATE expeditions SET name=?,region=?,start_date=?,end_date=?,status=?,description=? WHERE id=?",
        data.name, data.region, data.start_date, data.end_date, data.status, data.description, item_id,
    )
    await broadcast(env, item_id, "expedition.updated", "expedition", item_id)
    return {"ok": True}


@app.get("/api/locations")
async def list_locations(expedition_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    return await q_all(env, "SELECT * FROM locations WHERE expedition_id=? ORDER BY name", expedition_id)


@app.post("/api/locations")
async def add_location(data: LocationIn, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, data.expedition_id)
    try:
        r = await q_write(
            env,
            "INSERT INTO locations(expedition_id,name,type,latitude,longitude,source) VALUES(?,?,?,?,?,'manual')",
            data.expedition_id, data.name, data.type, data.latitude, data.longitude,
        )
    except Exception:
        raise HTTPException(409, "Location already exists")
    lid = last_row_id(r)
    await log_activity(env, data.expedition_id, "location", f"Location added: {data.name}", user["id"])
    await broadcast(env, data.expedition_id, "location.created", "location", lid)
    return {"id": lid}


@app.patch("/api/locations/{item_id}")
async def edit_location(item_id: int, data: LocationIn, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id FROM locations WHERE id=?", item_id)
    if not row:
        raise HTTPException(404, "Location not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    if int(data.expedition_id) != int(row["expedition_id"]):
        raise HTTPException(400, "Location cannot be moved between expeditions")
    await q_write(env, "UPDATE locations SET name=?,type=?,latitude=?,longitude=? WHERE id=?", data.name, data.type, data.latitude, data.longitude, item_id)
    await log_activity(env, row["expedition_id"], "location", f"Location updated: {data.name}", user["id"])
    await broadcast(env, row["expedition_id"], "location.updated", "location", item_id)
    return {"ok": True}


# ----------------------------- Telemetry ----------------------------------

async def insert_telemetry(env, data: TelemetryPositionIn) -> int:
    if data.entity_type not in ("personnel", "vehicle"):
        raise HTTPException(400, "entity_type must be personnel or vehicle")
    table = "personnel" if data.entity_type == "personnel" else "vehicles"
    row = await q_first(env, f"SELECT id,expedition_id FROM {table} WHERE id=?", data.entity_id)
    if not row:
        raise HTTPException(404, f"{data.entity_type.title()} not found")
    if int(row["expedition_id"]) != int(data.expedition_id):
        raise HTTPException(400, "Entity does not belong to this expedition")
    r = await q_write(
        env,
        "INSERT INTO telemetry_positions(expedition_id,entity_type,entity_id,latitude,longitude,altitude_m,accuracy_m,speed_kph,heading,source,recorded_at,received_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        data.expedition_id, data.entity_type, data.entity_id, data.latitude, data.longitude, data.altitude_m,
        data.accuracy_m, data.speed_kph, data.heading, data.source, data.recorded_at or utcnow(), utcnow(),
    )
    if data.entity_type == "vehicle" and data.fuel_percent is not None:
        await q_write(env, "UPDATE vehicles SET fuel_percent=? WHERE id=?", data.fuel_percent, data.entity_id)
    return last_row_id(r)


@app.post("/api/telemetry/position")
async def telemetry_position(data: TelemetryPositionIn, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, data.expedition_id)
    pid = await insert_telemetry(env, data)
    await broadcast(env, data.expedition_id, "telemetry.updated", data.entity_type, data.entity_id, {
        "position_id": pid, "latitude": data.latitude, "longitude": data.longitude,
        "accuracy_m": data.accuracy_m, "speed_kph": data.speed_kph, "heading": data.heading,
        "fuel_percent": data.fuel_percent, "source": data.source, "recorded_at": data.recorded_at or utcnow(),
    })
    return {"ok": True, "id": pid}


@app.post("/api/telemetry/batch")
async def telemetry_batch(data: TelemetryBatchIn, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    seen = set()
    latest = []
    for pos in data.positions:
        if pos.expedition_id not in seen:
            await ensure_expedition_access(env, user, pos.expedition_id)
            seen.add(pos.expedition_id)
        pid = await insert_telemetry(env, pos)
        latest.append({"expedition_id": pos.expedition_id, "entity_type": pos.entity_type, "entity_id": pos.entity_id, "position_id": pid})
    for exp_id in seen:
        await broadcast(env, exp_id, "telemetry.batch", "telemetry", None, {"positions": [x for x in latest if x["expedition_id"] == exp_id]})
    return {"ok": True, "count": len(data.positions)}


@app.get("/api/telemetry/latest")
async def telemetry_latest(expedition_id: int, request: Request, entity_type: Optional[str] = None, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    if entity_type and entity_type not in ("personnel", "vehicle"):
        raise HTTPException(400, "entity_type must be personnel or vehicle")
    return await latest_telemetry(env, expedition_id, entity_type)


@app.get("/api/telemetry/history")
async def telemetry_history(expedition_id: int, entity_type: str, entity_id: int, request: Request, limit: int = 200, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    if entity_type not in ("personnel", "vehicle"):
        raise HTTPException(400, "entity_type must be personnel or vehicle")
    return await q_all(env, "SELECT * FROM telemetry_positions WHERE expedition_id=? AND entity_type=? AND entity_id=? ORDER BY id DESC LIMIT ?", expedition_id, entity_type, entity_id, min(max(limit, 1), 2000))


# ----------------------------- Dashboard/personnel ------------------------

@app.get("/api/dashboard")
async def dashboard(expedition_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    exp = await ensure_expedition_access(env, user, expedition_id)
    latest_sql = """
        SELECT t.* FROM telemetry_positions t
        JOIN (
          SELECT entity_type,entity_id,MAX(id) max_id
          FROM telemetry_positions
          WHERE expedition_id=? AND entity_type=?
          GROUP BY entity_type,entity_id
        ) latest ON latest.max_id=t.id
        ORDER BY t.entity_type,t.entity_id
    """
    (
        personnel, cargo, inventory, vehicles, locations,
        incidents, activity, assets_rows, ppos, vpos,
    ) = await q_batch(env, [
        ("SELECT p.*,l.name location_name,l.latitude,l.longitude FROM personnel p LEFT JOIN locations l ON l.id=p.location_id WHERE p.expedition_id=?", (expedition_id,)),
        ("SELECT c.*,l.name location_name,d.name destination_name FROM cargo c LEFT JOIN locations l ON l.id=c.current_location_id LEFT JOIN locations d ON d.id=c.destination_location_id WHERE c.expedition_id=?", (expedition_id,)),
        ("SELECT i.*,l.name location_name FROM inventory_items i LEFT JOIN locations l ON l.id=i.location_id WHERE i.expedition_id=?", (expedition_id,)),
        ("SELECT v.*,l.name location_name,l.latitude,l.longitude FROM vehicles v LEFT JOIN locations l ON l.id=v.location_id WHERE v.expedition_id=?", (expedition_id,)),
        ("SELECT * FROM locations WHERE expedition_id=?", (expedition_id,)),
        ("SELECT i.*,l.name location_name FROM incidents i LEFT JOIN locations l ON l.id=i.location_id WHERE i.expedition_id=? ORDER BY i.id DESC", (expedition_id,)),
        ("SELECT a.*,u.name user_name FROM activity a LEFT JOIN users u ON u.id=a.user_id WHERE a.expedition_id=? ORDER BY a.id DESC LIMIT 12", (expedition_id,)),
        ("SELECT COUNT(*) c FROM assets WHERE expedition_id=?", (expedition_id,)),
        (latest_sql, (expedition_id, "personnel")),
        (latest_sql, (expedition_id, "vehicle")),
    ])
    assets_count = int(assets_rows[0].get("c") if assets_rows else 0)
    apply_live_positions(personnel, ppos)
    apply_live_positions(vehicles, vpos)

    now = datetime.now(timezone.utc)
    overdue = []
    for p in personnel:
        try:
            mins = (now - datetime.fromisoformat(p["last_checkin"])).total_seconds() / 60 if p.get("last_checkin") else 9999
        except Exception:
            mins = 9999
        p["checkin_minutes"] = round(mins)
        if mins > 45 or str(p.get("status", "")).lower() in ("check-in due", "unknown", "missing"):
            overdue.append(p)

    low_inventory = [i for i in inventory if float(i.get("quantity") or 0) < float(i.get("min_quantity") or 0)]
    low_fuel = [v for v in vehicles if float(v.get("fuel_percent") or 0) < 30]
    risks = []
    for p in overdue:
        risks.append({"severity": "high", "title": f"{p['name']} check-in overdue", "detail": f"Last known at {p.get('location_name') or 'unknown location'}"})
    for i in low_inventory:
        risks.append({"severity": "high", "title": f"{i['name']} below safety stock", "detail": f"{i['quantity']} {i['unit']} available; minimum {i['min_quantity']}"})
    for v in low_fuel:
        risks.append({"severity": "medium", "title": f"{v['code']} low fuel", "detail": f"{v['fuel_percent']}% fuel reserve"})

    fuel_avg = round(sum(float(v.get("fuel_percent") or 0) for v in vehicles) / len(vehicles)) if vehicles else 0
    return {
        "expedition": exp,
        "stats": {
            "personnel_total": len(personnel), "personnel_overdue": len(overdue),
            "cargo_total": len(cargo), "cargo_delivered": len([c for c in cargo if str(c.get("status", "")).lower() == "delivered"]),
            "fuel_avg": fuel_avg, "inventory_alerts": len(low_inventory),
            "vehicles_total": len(vehicles), "vehicles_operational": len([v for v in vehicles if str(v.get("status", "")).lower() == "operational"]),
            "active_incidents": len([i for i in incidents if str(i.get("status", "")).lower() != "resolved"]),
            "assets_total": assets_count, "live_personnel": len(ppos), "live_vehicles": len(vpos),
        },
        "locations": locations, "vehicles": vehicles, "personnel": personnel, "risks": risks, "activity": activity,
    }


@app.get("/api/personnel")
async def list_personnel(expedition_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    rows = await q_all(env, "SELECT p.*,l.name location_name,l.latitude,l.longitude FROM personnel p LEFT JOIN locations l ON l.id=p.location_id WHERE p.expedition_id=? ORDER BY p.name", expedition_id)
    return apply_live_positions(rows, await latest_telemetry(env, expedition_id, "personnel"))


@app.post("/api/personnel")
async def add_personnel(data: PersonnelIn, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, data.expedition_id)
    if not await valid_location_ids(env, data.expedition_id, data.location_id):
        raise HTTPException(400, "Personnel location must belong to this expedition")
    r = await q_write(env, "INSERT INTO personnel(expedition_id,name,role,team,location_id,status,last_checkin,contact,clearance_status,source,is_synthetic,created_at) VALUES(?,?,?,?,?,?,?,?,?,'manual',0,?)", data.expedition_id, data.name, data.role, data.team, data.location_id, data.status, utcnow(), data.contact, data.clearance_status, utcnow())
    pid = last_row_id(r)
    await log_activity(env, data.expedition_id, "personnel", f"Personnel added: {data.name}", user["id"])
    await broadcast(env, data.expedition_id, "personnel.created", "personnel", pid)
    return {"id": pid}


@app.patch("/api/personnel/{item_id}")
async def edit_personnel(item_id: int, data: PersonnelPatch, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id,name FROM personnel WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Personnel not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    if not await valid_location_ids(env, row["expedition_id"], data.location_id):
        raise HTTPException(400, "Personnel location must belong to this expedition")
    await patch_row(env, "personnel", item_id, data.model_dump(), {"name", "role", "team", "location_id", "status", "contact", "clearance_status"})
    await log_activity(env, row["expedition_id"], "personnel", f"{row['name']} personnel record updated", user["id"])
    await broadcast(env, row["expedition_id"], "personnel.updated", "personnel", item_id)
    return {"ok": True}


@app.post("/api/personnel/{item_id}/checkin")
async def checkin(item_id: int, data: CheckinIn, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id,name,location_id FROM personnel WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Personnel not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    loc = data.location_id if data.location_id is not None else row.get("location_id")
    if not await valid_location_ids(env, row["expedition_id"], loc):
        raise HTTPException(400, "Check-in location must belong to this expedition")
    now = utcnow()
    await q_write(env, "UPDATE personnel SET location_id=?,status=?,last_checkin=? WHERE id=?", loc, data.status, now, item_id)
    lname = await q_first(env, "SELECT name FROM locations WHERE id=?", loc) if loc else None
    await log_activity(env, row["expedition_id"], "personnel", f"{row['name']} checked in at {lname['name'] if lname else 'current location'}", user["id"])
    await broadcast(env, row["expedition_id"], "personnel.checkin", "personnel", item_id)
    return {"ok": True, "time": now}


@app.delete("/api/personnel/{item_id}")
async def delete_personnel(item_id: int, request: Request, user=Depends(require("commander"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id,name FROM personnel WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Personnel not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    await q_write(env, "DELETE FROM personnel WHERE id=?", item_id)
    await log_activity(env, row["expedition_id"], "personnel", f"{row['name']} removed from personnel roster", user["id"])
    await broadcast(env, row["expedition_id"], "personnel.deleted", "personnel", item_id)
    return {"ok": True}


# ----------------------------- Cargo/inventory ----------------------------

@app.get("/api/cargo")
async def list_cargo(expedition_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    return await q_all(env, """SELECT c.*,o.name origin_name,d.name destination_name,l.name location_name FROM cargo c
        LEFT JOIN locations o ON o.id=c.origin_location_id LEFT JOIN locations d ON d.id=c.destination_location_id
        LEFT JOIN locations l ON l.id=c.current_location_id WHERE c.expedition_id=? ORDER BY c.id DESC""", expedition_id)


@app.post("/api/cargo")
async def add_cargo(data: CargoIn, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, data.expedition_id)
    if not await valid_location_ids(
        env,
        data.expedition_id,
        data.origin_location_id,
        data.destination_location_id,
        data.current_location_id,
    ):
        raise HTTPException(400, "Cargo locations must belong to this expedition")
    now = utcnow()
    try:
        results = await q_write_batch(env, [
            ("INSERT INTO cargo(expedition_id,code,name,priority,origin_location_id,destination_location_id,current_location_id,status,quantity,unit,assigned_to,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
             (data.expedition_id, data.code, data.name, data.priority, data.origin_location_id, data.destination_location_id, data.current_location_id, data.status, data.quantity, data.unit, data.assigned_to, now)),
            ("INSERT INTO cargo_events(cargo_id,location_id,event_type,note,user_id,created_at) SELECT id,?,?,?,?,? FROM cargo WHERE expedition_id=? AND code=?",
             (data.current_location_id, "Registered", "Cargo registered", user["id"], now, data.expedition_id, data.code)),
            ("INSERT INTO activity(expedition_id,category,message,user_id,created_at) VALUES(?,?,?,?,?)",
             (data.expedition_id, "cargo", f"Cargo {data.code} registered: {data.name}", user["id"], now)),
        ])
    except Exception:
        raise HTTPException(409, "Cargo code already exists")
    cid = last_row_id(results[0])
    await broadcast(env, data.expedition_id, "cargo.created", "cargo", cid)
    return {"id": cid}


@app.patch("/api/cargo/{item_id}")
async def edit_cargo(item_id: int, data: CargoPatch, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id,code FROM cargo WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Cargo not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    if not await valid_location_ids(env, row["expedition_id"], data.destination_location_id):
        raise HTTPException(400, "Cargo destination must belong to this expedition")
    await patch_row(env, "cargo", item_id, data.model_dump(), {"name", "priority", "destination_location_id", "assigned_to"})
    await log_activity(env, row["expedition_id"], "cargo", f"Cargo {row['code']} updated", user["id"])
    await broadcast(env, row["expedition_id"], "cargo.updated", "cargo", item_id)
    return {"ok": True}


@app.post("/api/cargo/{item_id}/move")
async def move_cargo(item_id: int, data: CargoMove, request: Request, user=Depends(require("commander", "logistics", "field"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id,code FROM cargo WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Cargo not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    loc = await q_first(env, "SELECT name,expedition_id FROM locations WHERE id=?", data.location_id)
    if not loc or int(loc["expedition_id"]) != int(row["expedition_id"]):
        raise HTTPException(400, "Cargo destination must belong to this expedition")
    now = utcnow()
    message = f"Cargo {row['code']} moved to {loc['name']}"
    await q_write_batch(env, [
        ("UPDATE cargo SET current_location_id=?,status=? WHERE id=?", (data.location_id, data.status, item_id)),
        ("INSERT INTO cargo_events(cargo_id,location_id,event_type,note,user_id,created_at) VALUES(?,?,?,?,?,?)",
         (item_id, data.location_id, data.status, data.note, user["id"], now)),
        ("INSERT INTO activity(expedition_id,category,message,user_id,created_at) VALUES(?,?,?,?,?)",
         (row["expedition_id"], "cargo", message, user["id"], now)),
    ])
    await broadcast(env, row["expedition_id"], "cargo.moved", "cargo", item_id)
    return {"ok": True}


@app.get("/api/cargo/{item_id}/events")
async def cargo_events(item_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id FROM cargo WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Cargo not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    return await q_all(env, "SELECT e.*,l.name location_name,u.name user_name FROM cargo_events e LEFT JOIN locations l ON l.id=e.location_id LEFT JOIN users u ON u.id=e.user_id WHERE e.cargo_id=? ORDER BY e.id DESC", item_id)


@app.get("/api/inventory")
async def list_inventory(expedition_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    return await q_all(env, "SELECT i.*,l.name location_name FROM inventory_items i LEFT JOIN locations l ON l.id=i.location_id WHERE i.expedition_id=? ORDER BY i.name", expedition_id)


@app.post("/api/inventory")
async def add_inventory(data: InventoryIn, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, data.expedition_id)
    if not await valid_location_ids(env, data.expedition_id, data.location_id):
        raise HTTPException(400, "Inventory location must belong to this expedition")
    try:
        r = await q_write(env, "INSERT INTO inventory_items(expedition_id,sku,name,location_id,quantity,min_quantity,unit,expiry_date,created_at) VALUES(?,?,?,?,?,?,?,?,?)", data.expedition_id, data.sku, data.name, data.location_id, data.quantity, data.min_quantity, data.unit, data.expiry_date, utcnow())
    except Exception:
        raise HTTPException(409, "Inventory SKU already exists")
    iid = last_row_id(r)
    await log_activity(env, data.expedition_id, "inventory", f"Inventory item added: {data.name}", user["id"])
    await broadcast(env, data.expedition_id, "inventory.created", "inventory", iid)
    return {"id": iid}


@app.patch("/api/inventory/{item_id}")
async def edit_inventory(item_id: int, data: InventoryPatch, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id,name FROM inventory_items WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Inventory item not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    if not await valid_location_ids(env, row["expedition_id"], data.location_id):
        raise HTTPException(400, "Inventory location must belong to this expedition")
    await patch_row(env, "inventory_items", item_id, data.model_dump(), {"name", "location_id", "min_quantity", "unit", "expiry_date"})
    await log_activity(env, row["expedition_id"], "inventory", f"Inventory item {row['name']} updated", user["id"])
    await broadcast(env, row["expedition_id"], "inventory.updated", "inventory", item_id)
    return {"ok": True}


@app.post("/api/inventory/{item_id}/adjust")
async def adjust_inventory(item_id: int, data: InventoryAdjust, request: Request, user=Depends(require("commander", "logistics", "field"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id,name,quantity FROM inventory_items WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Inventory item not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    new_qty = float(row["quantity"] or 0) + data.delta
    if new_qty < 0: raise HTTPException(400, "Inventory cannot be negative")
    now = utcnow()
    await q_write_batch(env, [
        ("UPDATE inventory_items SET quantity=? WHERE id=?", (new_qty, item_id)),
        ("INSERT INTO inventory_events(inventory_id,delta,reason,user_id,created_at) VALUES(?,?,?,?,?)",
         (item_id, data.delta, data.reason, user["id"], now)),
        ("INSERT INTO activity(expedition_id,category,message,user_id,created_at) VALUES(?,?,?,?,?)",
         (row["expedition_id"], "inventory", f"{row['name']} adjusted by {data.delta:g}: {data.reason}", user["id"], now)),
    ])
    await broadcast(env, row["expedition_id"], "inventory.adjusted", "inventory", item_id, {"quantity": new_qty})
    return {"ok": True, "quantity": new_qty}


# ----------------------------- Vehicles/assets ----------------------------

@app.get("/api/vehicles")
async def list_vehicles(expedition_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    rows = await q_all(env, "SELECT v.*,l.name location_name,l.latitude,l.longitude FROM vehicles v LEFT JOIN locations l ON l.id=v.location_id WHERE v.expedition_id=? ORDER BY v.code", expedition_id)
    return apply_live_positions(rows, await latest_telemetry(env, expedition_id, "vehicle"))


@app.post("/api/vehicles")
async def add_vehicle(data: VehicleIn, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, data.expedition_id)
    if not await valid_location_ids(env, data.expedition_id, data.location_id):
        raise HTTPException(400, "Vehicle location must belong to this expedition")
    try:
        r = await q_write(env, "INSERT INTO vehicles(expedition_id,code,name,type,location_id,status,fuel_percent,range_km,created_at) VALUES(?,?,?,?,?,?,?,?,?)", data.expedition_id, data.code, data.name, data.type, data.location_id, data.status, data.fuel_percent, data.range_km, utcnow())
    except Exception:
        raise HTTPException(409, "Vehicle code already exists")
    vid = last_row_id(r)
    await log_activity(env, data.expedition_id, "vehicle", f"Vehicle {data.code} added", user["id"])
    await broadcast(env, data.expedition_id, "vehicle.created", "vehicle", vid)
    return {"id": vid}


@app.patch("/api/vehicles/{item_id}")
async def edit_vehicle(item_id: int, data: VehiclePatch, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id,code FROM vehicles WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Vehicle not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    if not await valid_location_ids(env, row["expedition_id"], data.location_id):
        raise HTTPException(400, "Vehicle location must belong to this expedition")
    await patch_row(env, "vehicles", item_id, data.model_dump(), {"name", "type", "location_id", "status", "fuel_percent", "range_km"})
    await log_activity(env, row["expedition_id"], "vehicle", f"Vehicle {row['code']} updated", user["id"])
    await broadcast(env, row["expedition_id"], "vehicle.updated", "vehicle", item_id)
    return {"ok": True}


@app.get("/api/assets")
async def list_assets(expedition_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    return await q_all(env, "SELECT a.*,l.name location_name,p.name assigned_to_name FROM assets a LEFT JOIN locations l ON l.id=a.location_id LEFT JOIN personnel p ON p.id=a.assigned_to_personnel_id WHERE a.expedition_id=? ORDER BY a.code", expedition_id)


@app.post("/api/assets")
async def add_asset(data: AssetIn, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, data.expedition_id)
    if not await valid_location_ids(env, data.expedition_id, data.location_id):
        raise HTTPException(400, "Asset location must belong to this expedition")
    if not await valid_personnel_ids(env, data.expedition_id, data.assigned_to_personnel_id):
        raise HTTPException(400, "Assigned personnel must belong to this expedition")
    try:
        r = await q_write(env, "INSERT INTO assets(expedition_id,code,name,category,location_id,status,serial_number,assigned_to_personnel_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)", data.expedition_id, data.code, data.name, data.category, data.location_id, data.status, data.serial_number, data.assigned_to_personnel_id, utcnow())
    except Exception:
        raise HTTPException(409, "Asset code already exists")
    aid = last_row_id(r)
    await log_activity(env, data.expedition_id, "asset", f"Asset {data.code} added: {data.name}", user["id"])
    await broadcast(env, data.expedition_id, "asset.created", "asset", aid)
    return {"id": aid}


@app.patch("/api/assets/{item_id}")
async def edit_asset(item_id: int, data: AssetPatch, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id,code FROM assets WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Asset not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
    if not await valid_location_ids(env, row["expedition_id"], data.location_id):
        raise HTTPException(400, "Asset location must belong to this expedition")
    if not await valid_personnel_ids(env, row["expedition_id"], data.assigned_to_personnel_id):
        raise HTTPException(400, "Assigned personnel must belong to this expedition")
    await patch_row(env, "assets", item_id, data.model_dump(), {"name", "category", "location_id", "status", "serial_number", "assigned_to_personnel_id"})
    await log_activity(env, row["expedition_id"], "asset", f"Asset {row['code']} updated", user["id"])
    await broadcast(env, row["expedition_id"], "asset.updated", "asset", item_id)
    return {"ok": True}


# ----------------------------- Incidents ----------------------------------

@app.get("/api/incidents")
async def list_incidents(expedition_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    return await q_all(env, """SELECT i.*,l.name location_name,v.code vehicle_code,v.name vehicle_name FROM incidents i
      LEFT JOIN locations l ON l.id=i.location_id LEFT JOIN vehicles v ON v.id=i.assigned_vehicle_id
      WHERE i.expedition_id=? ORDER BY i.id DESC""", expedition_id)


@app.post("/api/incidents")
async def add_incident(data: IncidentIn, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, data.expedition_id)
    if not await valid_location_ids(env, data.expedition_id, data.location_id):
        raise HTTPException(400, "Incident location must belong to this expedition")
    seq = int(await q_value(env, "SELECT COUNT(*) c FROM incidents WHERE expedition_id=?", data.expedition_id) or 0) + 1
    code = f"INC-{seq:03d}"
    now = utcnow()
    results = await q_write_batch(env, [
        ("INSERT INTO incidents(expedition_id,code,title,type,severity,location_id,status,description,affected_count,created_by,created_at) VALUES(?,?,?,?,?,?,'Active',?,?,?,?)",
         (data.expedition_id, code, data.title, data.type, data.severity, data.location_id, data.description, data.affected_count, user["id"], now)),
        ("INSERT INTO incident_events(incident_id,event_type,note,user_id,created_at) SELECT id,?,?,?,? FROM incidents WHERE expedition_id=? AND code=?",
         ("SOS", "Incident reported", user["id"], now, data.expedition_id, code)),
        ("INSERT INTO activity(expedition_id,category,message,user_id,created_at) VALUES(?,?,?,?,?)",
         (data.expedition_id, "incident", f"{code} triggered at incident location", user["id"], now)),
    ])
    iid = last_row_id(results[0])
    await broadcast(env, data.expedition_id, "incident.created", "incident", iid, {"code": code, "severity": data.severity})
    return {"id": iid, "code": code}


@app.get("/api/incidents/{item_id}")
async def incident_detail(item_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    inc = await q_first(env, """SELECT i.*,l.name location_name,l.latitude,l.longitude,v.code vehicle_code,v.name vehicle_name FROM incidents i
      LEFT JOIN locations l ON l.id=i.location_id LEFT JOIN vehicles v ON v.id=i.assigned_vehicle_id WHERE i.id=?""", item_id)
    if not inc: raise HTTPException(404, "Incident not found")
    await ensure_expedition_access(env, user, inc["expedition_id"])
    events = await q_all(env, "SELECT e.*,u.name user_name FROM incident_events e LEFT JOIN users u ON u.id=e.user_id WHERE e.incident_id=? ORDER BY e.id", item_id)
    med = await q_all(env, "SELECT i.*,l.name location_name FROM inventory_items i LEFT JOIN locations l ON l.id=i.location_id WHERE i.expedition_id=? AND lower(i.name) LIKE '%medical%'", inc["expedition_id"])
    vehicles = await q_all(env, "SELECT v.*,l.name location_name,l.latitude,l.longitude FROM vehicles v LEFT JOIN locations l ON l.id=v.location_id WHERE v.expedition_id=? AND lower(v.status)='operational'", inc["expedition_id"])
    apply_live_positions(vehicles, await latest_telemetry(env, inc["expedition_id"], "vehicle"))
    people = await q_all(env, "SELECT p.*,l.name location_name FROM personnel p LEFT JOIN locations l ON l.id=p.location_id WHERE p.expedition_id=? AND p.location_id IS ?", inc["expedition_id"], inc.get("location_id"))
    nearest = None
    for v in vehicles:
        dist = haversine_km(inc.get("latitude"), inc.get("longitude"), v.get("latitude"), v.get("longitude"))
        if dist is not None and (nearest is None or dist < nearest["distance_km"]):
            nearest = {"vehicle": v, "distance_km": round(dist, 1)}
    return {"incident": inc, "events": events, "medical_inventory": med, "personnel_at_location": people, "nearest_vehicle": nearest}


@app.post("/api/incidents/{item_id}/dispatch")
async def dispatch_incident(item_id: int, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    inc = await q_first(env, "SELECT i.*,l.latitude,l.longitude FROM incidents i LEFT JOIN locations l ON l.id=i.location_id WHERE i.id=?", item_id)
    if not inc: raise HTTPException(404, "Incident not found")
    await ensure_expedition_access(env, user, inc["expedition_id"])
    vehicles = await q_all(env, "SELECT v.*,l.latitude,l.longitude FROM vehicles v LEFT JOIN locations l ON l.id=v.location_id WHERE v.expedition_id=? AND lower(v.status)='operational'", inc["expedition_id"])
    apply_live_positions(vehicles, await latest_telemetry(env, inc["expedition_id"], "vehicle"))
    best = None
    for v in vehicles:
        d = haversine_km(inc.get("latitude"), inc.get("longitude"), v.get("latitude"), v.get("longitude"))
        if d is not None and (best is None or d < best[0]): best = (d, v)
    if not best: raise HTTPException(400, "No operational vehicle with valid coordinates available")
    d, v = best
    note = f"Response vehicle {v['code']} dispatched; estimated straight-line distance {d:.1f} km"
    now = utcnow()
    await q_write_batch(env, [
        ("UPDATE incidents SET status='Response Dispatched',assigned_vehicle_id=? WHERE id=?", (v["id"], item_id)),
        ("INSERT INTO incident_events(incident_id,event_type,note,user_id,created_at) VALUES(?,?,?,?,?)",
         (item_id, "Dispatch", note, user["id"], now)),
        ("INSERT INTO activity(expedition_id,category,message,user_id,created_at) VALUES(?,?,?,?,?)",
         (inc["expedition_id"], "incident", f"{inc['code']}: {note}", user["id"], now)),
    ])
    await broadcast(env, inc["expedition_id"], "incident.dispatched", "incident", item_id, {"vehicle_code": v["code"], "distance_km": round(d, 1)})
    return {"ok": True, "vehicle_code": v["code"], "distance_km": round(d, 1)}


@app.post("/api/incidents/{item_id}/resolve")
async def resolve_incident(item_id: int, request: Request, user=Depends(require("commander"))):
    env = request.scope["env"]
    inc = await q_first(env, "SELECT expedition_id,code FROM incidents WHERE id=?", item_id)
    if not inc: raise HTTPException(404, "Incident not found")
    await ensure_expedition_access(env, user, inc["expedition_id"])
    now = utcnow()
    await q_write_batch(env, [
        ("UPDATE incidents SET status='Resolved',resolved_at=? WHERE id=?", (now, item_id)),
        ("INSERT INTO incident_events(incident_id,event_type,note,user_id,created_at) VALUES(?,?,?,?,?)",
         (item_id, "Resolved", "Incident resolved; personnel accountability confirmed", user["id"], now)),
        ("INSERT INTO activity(expedition_id,category,message,user_id,created_at) VALUES(?,?,?,?,?)",
         (inc["expedition_id"], "incident", f"{inc['code']} resolved", user["id"], now)),
    ])
    await broadcast(env, inc["expedition_id"], "incident.resolved", "incident", item_id)
    return {"ok": True}


@app.post("/api/incidents/{item_id}/events")
async def add_incident_event(item_id: int, data: IncidentEventIn, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    inc = await q_first(env, "SELECT expedition_id,code FROM incidents WHERE id=?", item_id)
    if not inc: raise HTTPException(404, "Incident not found")
    await ensure_expedition_access(env, user, inc["expedition_id"])
    now = utcnow()
    await q_write_batch(env, [
        ("INSERT INTO incident_events(incident_id,event_type,note,user_id,created_at) VALUES(?,?,?,?,?)",
         (item_id, data.event_type, data.note, user["id"], now)),
        ("INSERT INTO activity(expedition_id,category,message,user_id,created_at) VALUES(?,?,?,?,?)",
         (inc["expedition_id"], "incident", f"{inc['code']}: {data.note}", user["id"], now)),
    ])
    await broadcast(env, inc["expedition_id"], "incident.event", "incident", item_id, {"event_type": data.event_type})
    return {"ok": True}


# ----------------------------- Authorized worker feed ---------------------

@app.get("/api/integrations/workers/status")
async def worker_feed_status(expedition_id: int, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    row = await q_first(env, "SELECT * FROM data_sources WHERE name='Worker Feed'")
    count = int(await q_value(env, "SELECT COUNT(*) c FROM personnel WHERE expedition_id=? AND source LIKE 'feed:%'", expedition_id) or 0)
    url = env_value(env, "OPERATIONS_FEED_URL")
    return {"configured": bool(url), "source": row, "workers": count, "url_host": urllib.parse.urlparse(url).netloc if url else ""}


@app.post("/api/integrations/workers/sync")
async def worker_feed_sync(expedition_id: int, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    url = env_value(env, "OPERATIONS_FEED_URL")
    if not url: raise HTTPException(400, "No authorized operations feed configured")
    headers = {}
    token = env_value(env, "OPERATIONS_FEED_TOKEN")
    if token: headers["Authorization"] = f"Bearer {token}"
    try:
        resp = await cf_fetch(url, headers=headers)
        if int(resp.status) >= 400: raise RuntimeError(f"HTTP {resp.status}")
        payload = to_py(await resp.json())
        workers = payload.get("workers", payload.get("personnel", [])) if isinstance(payload, dict) else payload
        locations = payload.get("locations", []) if isinstance(payload, dict) else []
        if not isinstance(workers, list) or not isinstance(locations, list): raise ValueError("Invalid operations feed shape")
        host = urllib.parse.urlparse(url).netloc or "authorized-feed"
        created = updated = telemetry = skipped = locations_synced = 0
        for item in locations:
            if not isinstance(item, dict): continue
            name = str(item.get("name") or item.get("location_name") or "").strip()
            if not name: continue
            typ = str(item.get("type") or item.get("location_type") or "External")[:80]
            lat, lon = parse_coord(item.get("latitude")), parse_coord(item.get("longitude"))
            existing = await q_first(env, "SELECT id FROM locations WHERE expedition_id=? AND name=?", expedition_id, name)
            if existing:
                await q_write(env, "UPDATE locations SET type=?,latitude=COALESCE(?,latitude),longitude=COALESCE(?,longitude),source=? WHERE id=?", typ, lat, lon, f"feed:{host}", existing["id"])
            else:
                await q_write(env, "INSERT INTO locations(expedition_id,name,type,latitude,longitude,source) VALUES(?,?,?,?,?,?)", expedition_id, name, typ, lat, lon, f"feed:{host}")
            locations_synced += 1
        for item in workers:
            if not isinstance(item, dict): skipped += 1; continue
            external_id = str(item.get("external_id") or item.get("id") or item.get("personnel_id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not external_id or not name: skipped += 1; continue
            role = str(item.get("role") or "Field Worker")[:120]
            team = str(item.get("team") or "")[:120]
            status = str(item.get("status") or "Safe")[:80]
            contact = str(item.get("contact") or "")[:200]
            clearance = str(item.get("clearance_status") or item.get("clearance") or "Cleared")[:80]
            synthetic = 1 if bool(item.get("is_synthetic") or item.get("synthetic")) else 0
            last_checkin = item.get("last_checkin") or item.get("timestamp")
            location_name = str(item.get("location_name") or item.get("camp") or item.get("base") or item.get("location") or "").strip()
            lat, lon = parse_coord(item.get("latitude")), parse_coord(item.get("longitude"))
            location_id = None
            if location_name:
                loc = await q_first(env, "SELECT id FROM locations WHERE expedition_id=? AND name=?", expedition_id, location_name)
                if loc: location_id = loc["id"]
                else:
                    rr = await q_write(env, "INSERT INTO locations(expedition_id,name,type,latitude,longitude,source) VALUES(?,?,?,?,?,?)", expedition_id, location_name, "External", lat, lon, f"feed:{host}")
                    location_id = last_row_id(rr)
            existing = await q_first(env, "SELECT id FROM personnel WHERE expedition_id=? AND external_id=?", expedition_id, external_id)
            if existing:
                await q_write(env, "UPDATE personnel SET name=?,role=?,team=?,location_id=COALESCE(?,location_id),status=?,contact=?,clearance_status=?,last_checkin=COALESCE(?,last_checkin),source=?,is_synthetic=? WHERE id=?", name, role, team, location_id, status, contact, clearance, last_checkin, f"feed:{host}", synthetic, existing["id"])
                pid = existing["id"]; updated += 1
            else:
                rr = await q_write(env, "INSERT INTO personnel(expedition_id,name,role,team,location_id,status,last_checkin,contact,clearance_status,external_id,source,is_synthetic,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", expedition_id, name, role, team, location_id, status, last_checkin, contact, clearance, external_id, f"feed:{host}", synthetic, utcnow())
                pid = last_row_id(rr); created += 1
            if lat is not None and lon is not None:
                await insert_telemetry(env, TelemetryPositionIn(expedition_id=expedition_id, entity_type="personnel", entity_id=pid, latitude=lat, longitude=lon, accuracy_m=item.get("accuracy_m"), speed_kph=item.get("speed_kph"), heading=item.get("heading"), source=f"worker-feed:{host}", recorded_at=item.get("recorded_at") or last_checkin))
                telemetry += 1
        details = f"{locations_synced} locations, {created} workers created, {updated} updated, {telemetry} live positions, {skipped} skipped"
        await source_status(env, "Worker Feed", url, "OK", details)
        await log_activity(env, expedition_id, "personnel", f"Authorized operations feed synchronized: {locations_synced} locations, {created} workers added, {updated} updated", user["id"])
        result = {"ok": True, "locations": locations_synced, "created": created, "updated": updated, "telemetry": telemetry, "skipped": skipped, "source_host": host}
        await broadcast(env, expedition_id, "personnel.external_sync", "personnel", None, result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        await source_status(env, "Worker Feed", url, "ERROR", str(exc)[:500])
        raise HTTPException(502, f"Worker feed sync failed: {exc}")


# ----------------------------- Activity/backup/health ---------------------

@app.get("/api/activity")
async def activity(expedition_id: int, request: Request, limit: int = 100, user=Depends(current_user)):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    return await q_all(env, "SELECT a.*,u.name user_name FROM activity a LEFT JOIN users u ON u.id=a.user_id WHERE a.expedition_id=? ORDER BY a.id DESC LIMIT ?", expedition_id, min(limit, 500))


@app.get("/api/backup")
async def backup(request: Request, user=Depends(require("commander"))):
    env = request.scope["env"]
    payload = {"exported_at": utcnow(), "version": APP_VERSION}
    org_id = user["organization_id"]
    # Global reference tables are exported; operational tables are restricted by tenant.
    payload["organizations"] = await q_all(env, "SELECT * FROM organizations WHERE id=?", org_id)
    payload["users"] = await q_all(env, "SELECT id,organization_id,email,name,role,active,created_at FROM users WHERE organization_id=?", org_id)
    exp_ids = [x["id"] for x in await q_all(env, "SELECT id FROM expeditions WHERE organization_id=?", org_id)]
    payload["expeditions"] = await q_all(env, "SELECT * FROM expeditions WHERE organization_id=?", org_id)
    for table in ["locations", "personnel", "cargo", "inventory_items", "vehicles", "assets", "incidents", "activity", "telemetry_positions", "mission_tasks", "planned_routes", "geofences", "ops_alerts", "science_records", "comms_checkins", "readiness_items", "shift_handovers", "audit_events"]:
        if exp_ids:
            placeholders = ",".join("?" for _ in exp_ids)
            payload[table] = await q_all(env, f"SELECT * FROM {table} WHERE expedition_id IN ({placeholders})", *exp_ids)
        else:
            payload[table] = []
    if exp_ids:
        placeholders = ",".join("?" for _ in exp_ids)
        payload["cargo_events"] = await q_all(env, f"SELECT e.* FROM cargo_events e JOIN cargo c ON c.id=e.cargo_id WHERE c.expedition_id IN ({placeholders})", *exp_ids)
        payload["inventory_events"] = await q_all(env, f"SELECT e.* FROM inventory_events e JOIN inventory_items i ON i.id=e.inventory_id WHERE i.expedition_id IN ({placeholders})", *exp_ids)
        payload["incident_events"] = await q_all(env, f"SELECT e.* FROM incident_events e JOIN incidents i ON i.id=e.incident_id WHERE i.expedition_id IN ({placeholders})", *exp_ids)
        payload["incident_actions"] = await q_all(env, f"SELECT a.* FROM incident_actions a JOIN incidents i ON i.id=a.incident_id WHERE i.expedition_id IN ({placeholders})", *exp_ids)
    else:
        payload["cargo_events"] = []
        payload["inventory_events"] = []
        payload["incident_events"] = []
        payload["incident_actions"] = []
    payload["public_facilities"] = await q_all(env, "SELECT * FROM public_facilities")
    payload["facility_weather"] = await q_all(env, "SELECT * FROM facility_weather")
    payload["data_sources"] = await q_all(env, "SELECT * FROM data_sources")
    payload["research_station_reference"] = await q_all(env, "SELECT * FROM research_station_reference")
    payload["arctic_research_stations"] = await q_all(env, "SELECT * FROM arctic_research_stations")
    raw = json.dumps(payload, separators=(",", ":"), default=str)
    try:
        backups = getattr(env, "BACKUPS", None)
        if backups is None:
            payload["r2_backup"] = {"status": "not_configured"}
        else:
            key = f"backups/org-{org_id}/polarops-{datetime.now(timezone.utc).date()}.json"
            await backups.put(key, raw)
            payload["r2_backup"] = {"status": "stored", "key": key}
    except Exception as exc:
        payload["r2_backup"] = {"status": "error", "detail": type(exc).__name__}
    return payload


# ----------------------------- Static frontend ----------------------------

@app.get("/")
async def root(request: Request):
    env = request.scope["env"]
    resp = await env.ASSETS.fetch("https://assets.local/index.html")
    body = await resp.bytes()
    return FastAPIResponse(content=bytes(body), status_code=int(resp.status), headers=dict(resp.headers))


@app.get("/{path:path}")
async def frontend(path: str, request: Request):
    if path.startswith("api/") or path.startswith("ws/"):
        raise HTTPException(404, "Not found")
    env = request.scope["env"]
    target = path or "index.html"
    resp = await env.ASSETS.fetch(f"https://assets.local/{target}")
    if int(resp.status) == 404:
        resp = await env.ASSETS.fetch("https://assets.local/index.html")
    body = await resp.bytes()
    return FastAPIResponse(content=bytes(body), status_code=int(resp.status), headers=dict(resp.headers))


# ----------------------------- Worker entrypoint ---------------------------

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        parsed = urllib.parse.urlparse(request.url)
        path = parsed.path
        if path.startswith("/ws/expeditions/"):
            parts = [p for p in path.split("/") if p]
            if len(parts) != 3:
                return Response("Invalid WebSocket path", status=404)
            try:
                expedition_id = int(parts[-1])
            except Exception:
                return Response("Invalid expedition", status=404)

            query = urllib.parse.parse_qs(parsed.query)
            ticket = (query.get("ticket") or [""])[0]
            try:
                payload = decode_ws_ticket(
                    ticket,
                    required_env_value(self.env, "AUTH_SECRET"),
                )
            except Exception:
                return Response("Unauthorized", status=401)

            if int(payload.get("eid", -1)) != expedition_id:
                return Response("Unauthorized", status=403)

            if not await realtime_ticket_allows_expedition(
                self.env,
                expedition_id,
                payload,
            ):
                return Response("Unauthorized", status=403)

            stub = self.env.EXPEDITION_ROOM.getByName(str(expedition_id))
            return await stub.fetch(request)
        return await asgi.fetch(app, request, self.env)
