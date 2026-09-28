from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import math
import re
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, Field
from workers import DurableObject, Response, WorkerEntrypoint, asgi, fetch as cf_fetch
from js import Buffer, Object, TextEncoder, Uint8Array, WebSocketPair, crypto
from pyodide.ffi import to_js as _to_js

APP_VERSION = "2.0.0-cloudflare"
DEFAULT_COMNAP_URL = "https://www.comnap.aq/s/Facilities_Nov2024.csv"
DEFAULT_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TOKEN_HOURS = 12

app = FastAPI(title="PolarOps Cloudflare", version=APP_VERSION)


# ----------------------------- Generic helpers -----------------------------

def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def env_value(env, name: str, default: str = "") -> str:
    try:
        value = getattr(env, name)
        if value is None:
            return default
        return str(value)
    except Exception:
        return default


def to_py(value):
    if value is None:
        return None
    try:
        return value.to_py()
    except Exception:
        return value


async def q_all(env, sql: str, *params) -> list[dict]:
    stmt = env.DB.prepare(sql)
    if params:
        stmt = stmt.bind(*params)
    result = await stmt.run()
    rows = to_py(result.results)
    return list(rows or [])


async def q_first(env, sql: str, *params) -> Optional[dict]:
    stmt = env.DB.prepare(sql)
    if params:
        stmt = stmt.bind(*params)
    row = await stmt.first()
    row = to_py(row)
    return dict(row) if row else None


async def q_value(env, sql: str, *params, key: str = "c"):
    row = await q_first(env, sql, *params)
    return row.get(key) if row else None


async def q_write(env, sql: str, *params):
    stmt = env.DB.prepare(sql)
    if params:
        stmt = stmt.bind(*params)
    return await stmt.run()


def last_row_id(result) -> int:
    try:
        return int(result.meta.last_row_id)
    except Exception:
        meta = to_py(getattr(result, "meta", {})) or {}
        return int(meta.get("last_row_id") or 0)


def _js_obj(value):
    return _to_js(value, dict_converter=Object.fromEntries)


async def _pbkdf2_bits(password: str, salt_js, rounds: int):
    # Do not keep JavaScript object instances in module-level globals.
    # Python Workers snapshot top-level state during deployment, and objects
    # such as TextEncoder instances are not serializable.
    encoder = TextEncoder.new()
    key = await crypto.subtle.importKey(
        "raw",
        encoder.encode(password),
        "PBKDF2",
        False,
        ["deriveBits"],
    )
    return await crypto.subtle.deriveBits(
        _js_obj({
            "name": "PBKDF2",
            "salt": salt_js,
            "iterations": int(rounds),
            "hash": "SHA-256",
        }),
        key,
        256,
    )


async def hash_password(password: str) -> str:
    # Python Workers run on Pyodide, where hashlib.pbkdf2_hmac depends on
    # OpenSSL functionality that is not available. Use Workers Web Crypto
    # instead; PBKDF2 is implemented natively by the Workers runtime.
    salt = crypto.getRandomValues(Uint8Array.new(16))
    digest = await _pbkdf2_bits(password, salt, 180_000)
    return (
        "pbkdf2_sha256$180000$"
        + str(getattr(Buffer, "from")(salt).toString("base64"))
        + "$"
        + str(getattr(Buffer, "from")(digest).toString("base64"))
    )


async def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds, salt_b64, digest_b64 = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        buffer_from = getattr(Buffer, "from")
        salt = buffer_from(salt_b64, "base64")
        expected = buffer_from(digest_b64, "base64")
        actual = await _pbkdf2_bits(password, salt, int(rounds))
        return bool(crypto.subtle.timingSafeEqual(actual, expected))
    except Exception:
        return False


def make_token(user: dict, secret: str) -> str:
    payload = {
        "uid": user["id"],
        "oid": user["organization_id"],
        "email": user["email"],
        "role": user["role"],
        "exp": int(time.time() + TOKEN_HOURS * 3600),
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()
    return raw + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def decode_token(token: str, secret: str) -> dict:
    try:
        raw, sig = token.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad signature")
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if payload.get("exp", 0) < time.time():
            raise ValueError("expired")
        return payload
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


async def current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    env = request.scope["env"]
    secret = env_value(env, "AUTH_SECRET", "dev-only-change-me")
    payload = decode_token(auth[7:], secret)
    user = await q_first(
        env,
        "SELECT id,organization_id,email,name,role,active,created_at FROM users WHERE id=?",
        payload["uid"],
    )
    if not user or not user.get("active"):
        raise HTTPException(status_code=401, detail="User is inactive")
    return user


def require(*roles: str):
    async def dep(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permission")
        return user
    return dep


async def ensure_expedition_access(env, user: dict, expedition_id: int) -> dict:
    exp = await q_first(env, "SELECT * FROM expeditions WHERE id=?", expedition_id)
    if not exp or int(exp["organization_id"]) != int(user["organization_id"]):
        raise HTTPException(status_code=404, detail="Expedition not found")
    return exp


async def log_activity(env, expedition_id: int, category: str, message: str, user_id: int | None = None):
    await q_write(
        env,
        "INSERT INTO activity(expedition_id,category,message,user_id,created_at) VALUES(?,?,?,?,?)",
        expedition_id, category, message, user_id, utcnow(),
    )


async def broadcast(env, expedition_id: int, event_type: str, entity_type: str, entity_id: int | None = None, data: dict | None = None):
    try:
        stub = env.EXPEDITION_ROOM.getByName(str(expedition_id))
        payload = json.dumps({
            "type": event_type,
            "expedition_id": expedition_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "server_time": utcnow(),
            "data": data or {},
        }, separators=(",", ":"))
        await stub.broadcast_json(payload)
    except Exception:
        # A failed real-time notification must never roll back the database mutation.
        pass


def haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_coord(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace(",", ".")
    try:
        return float(text)
    except Exception:
        pass
    nums = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", text)]
    if not nums:
        return None
    sign = -1 if any(x in text.upper() for x in ("S", "W")) else 1
    if nums[0] < 0:
        sign = -1
    deg = abs(nums[0])
    minutes = nums[1] if len(nums) > 1 else 0
    seconds = nums[2] if len(nums) > 2 else 0
    return sign * (deg + minutes / 60 + seconds / 3600)


def pick(row: dict, *names: str) -> str:
    normalized = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        value = normalized.get(name.strip().lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


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
        "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' ws: wss:; "
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
    token = make_token(row, env_value(env, "AUTH_SECRET", "dev-only-change-me"))
    return {"token": token, "user": {k: row[k] for k in ("id", "organization_id", "email", "name", "role")}}


@app.get("/api/me")
async def me(user=Depends(current_user)):
    return user


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
    return {"id": last_row_id(r)}


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
    personnel = await q_all(env, "SELECT p.*,l.name location_name,l.latitude,l.longitude FROM personnel p LEFT JOIN locations l ON l.id=p.location_id WHERE p.expedition_id=?", expedition_id)
    cargo = await q_all(env, "SELECT c.*,l.name location_name,d.name destination_name FROM cargo c LEFT JOIN locations l ON l.id=c.current_location_id LEFT JOIN locations d ON d.id=c.destination_location_id WHERE c.expedition_id=?", expedition_id)
    inventory = await q_all(env, "SELECT i.*,l.name location_name FROM inventory_items i LEFT JOIN locations l ON l.id=i.location_id WHERE i.expedition_id=?", expedition_id)
    vehicles = await q_all(env, "SELECT v.*,l.name location_name,l.latitude,l.longitude FROM vehicles v LEFT JOIN locations l ON l.id=v.location_id WHERE v.expedition_id=?", expedition_id)
    locations = await q_all(env, "SELECT * FROM locations WHERE expedition_id=?", expedition_id)
    incidents = await q_all(env, "SELECT i.*,l.name location_name FROM incidents i LEFT JOIN locations l ON l.id=i.location_id WHERE i.expedition_id=? ORDER BY i.id DESC", expedition_id)
    activity = await q_all(env, "SELECT a.*,u.name user_name FROM activity a LEFT JOIN users u ON u.id=a.user_id WHERE a.expedition_id=? ORDER BY a.id DESC LIMIT 12", expedition_id)
    assets_count = int(await q_value(env, "SELECT COUNT(*) c FROM assets WHERE expedition_id=?", expedition_id) or 0)
    ppos = await latest_telemetry(env, expedition_id, "personnel")
    vpos = await latest_telemetry(env, expedition_id, "vehicle")
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
    try:
        r = await q_write(env, "INSERT INTO cargo(expedition_id,code,name,priority,origin_location_id,destination_location_id,current_location_id,status,quantity,unit,assigned_to,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", data.expedition_id, data.code, data.name, data.priority, data.origin_location_id, data.destination_location_id, data.current_location_id, data.status, data.quantity, data.unit, data.assigned_to, utcnow())
    except Exception:
        raise HTTPException(409, "Cargo code already exists")
    cid = last_row_id(r)
    await q_write(env, "INSERT INTO cargo_events(cargo_id,location_id,event_type,note,user_id,created_at) VALUES(?,?,?,?,?,?)", cid, data.current_location_id, "Registered", "Cargo registered", user["id"], utcnow())
    await log_activity(env, data.expedition_id, "cargo", f"Cargo {data.code} registered: {data.name}", user["id"])
    await broadcast(env, data.expedition_id, "cargo.created", "cargo", cid)
    return {"id": cid}


@app.patch("/api/cargo/{item_id}")
async def edit_cargo(item_id: int, data: CargoPatch, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT expedition_id,code FROM cargo WHERE id=?", item_id)
    if not row: raise HTTPException(404, "Cargo not found")
    await ensure_expedition_access(env, user, row["expedition_id"])
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
    await q_write(env, "UPDATE cargo SET current_location_id=?,status=? WHERE id=?", data.location_id, data.status, item_id)
    await q_write(env, "INSERT INTO cargo_events(cargo_id,location_id,event_type,note,user_id,created_at) VALUES(?,?,?,?,?,?)", item_id, data.location_id, data.status, data.note, user["id"], utcnow())
    loc = await q_first(env, "SELECT name FROM locations WHERE id=?", data.location_id)
    await log_activity(env, row["expedition_id"], "cargo", f"Cargo {row['code']} moved to {loc['name'] if loc else 'new location'}", user["id"])
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
    await q_write(env, "UPDATE inventory_items SET quantity=? WHERE id=?", new_qty, item_id)
    await q_write(env, "INSERT INTO inventory_events(inventory_id,delta,reason,user_id,created_at) VALUES(?,?,?,?,?)", item_id, data.delta, data.reason, user["id"], utcnow())
    await log_activity(env, row["expedition_id"], "inventory", f"{row['name']} adjusted by {data.delta:g}: {data.reason}", user["id"])
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
    seq = int(await q_value(env, "SELECT COUNT(*) c FROM incidents WHERE expedition_id=?", data.expedition_id) or 0) + 1
    code = f"INC-{seq:03d}"
    r = await q_write(env, "INSERT INTO incidents(expedition_id,code,title,type,severity,location_id,status,description,affected_count,created_by,created_at) VALUES(?,?,?,?,?,?,'Active',?,?,?,?)", data.expedition_id, code, data.title, data.type, data.severity, data.location_id, data.description, data.affected_count, user["id"], utcnow())
    iid = last_row_id(r)
    await q_write(env, "INSERT INTO incident_events(incident_id,event_type,note,user_id,created_at) VALUES(?,?,?,?,?)", iid, "SOS", "Incident reported", user["id"], utcnow())
    await log_activity(env, data.expedition_id, "incident", f"{code} triggered at incident location", user["id"])
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
    await q_write(env, "UPDATE incidents SET status='Response Dispatched',assigned_vehicle_id=? WHERE id=?", v["id"], item_id)
    note = f"Response vehicle {v['code']} dispatched; estimated straight-line distance {d:.1f} km"
    await q_write(env, "INSERT INTO incident_events(incident_id,event_type,note,user_id,created_at) VALUES(?,?,?,?,?)", item_id, "Dispatch", note, user["id"], utcnow())
    await log_activity(env, inc["expedition_id"], "incident", f"{inc['code']}: {note}", user["id"])
    await broadcast(env, inc["expedition_id"], "incident.dispatched", "incident", item_id, {"vehicle_code": v["code"], "distance_km": round(d, 1)})
    return {"ok": True, "vehicle_code": v["code"], "distance_km": round(d, 1)}


@app.post("/api/incidents/{item_id}/resolve")
async def resolve_incident(item_id: int, request: Request, user=Depends(require("commander"))):
    env = request.scope["env"]
    inc = await q_first(env, "SELECT expedition_id,code FROM incidents WHERE id=?", item_id)
    if not inc: raise HTTPException(404, "Incident not found")
    await ensure_expedition_access(env, user, inc["expedition_id"])
    await q_write(env, "UPDATE incidents SET status='Resolved',resolved_at=? WHERE id=?", utcnow(), item_id)
    await q_write(env, "INSERT INTO incident_events(incident_id,event_type,note,user_id,created_at) VALUES(?,?,?,?,?)", item_id, "Resolved", "Incident resolved; personnel accountability confirmed", user["id"], utcnow())
    await log_activity(env, inc["expedition_id"], "incident", f"{inc['code']} resolved", user["id"])
    await broadcast(env, inc["expedition_id"], "incident.resolved", "incident", item_id)
    return {"ok": True}


@app.post("/api/incidents/{item_id}/events")
async def add_incident_event(item_id: int, data: IncidentEventIn, request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    inc = await q_first(env, "SELECT expedition_id,code FROM incidents WHERE id=?", item_id)
    if not inc: raise HTTPException(404, "Incident not found")
    await ensure_expedition_access(env, user, inc["expedition_id"])
    await q_write(env, "INSERT INTO incident_events(incident_id,event_type,note,user_id,created_at) VALUES(?,?,?,?,?)", item_id, data.event_type, data.note, user["id"], utcnow())
    await log_activity(env, inc["expedition_id"], "incident", f"{inc['code']}: {data.note}", user["id"])
    await broadcast(env, inc["expedition_id"], "incident.event", "incident", item_id, {"event_type": data.event_type})
    return {"ok": True}


# ----------------------------- Public Antarctic data ----------------------

async def source_status(env, name: str, url: str, status: str, details: str = ""):
    await q_write(env, """INSERT INTO data_sources(name,source_url,last_sync,last_status,details) VALUES(?,?,?,?,?)
      ON CONFLICT(name) DO UPDATE SET source_url=excluded.source_url,last_sync=excluded.last_sync,last_status=excluded.last_status,details=excluded.details""",
      name, url, utcnow(), status, details)


async def sync_comnap(env, offset: int = 0, limit: int = 35) -> dict:
    url = env_value(env, "COMNAP_FACILITIES_URL", DEFAULT_COMNAP_URL)
    try:
        resp = await cf_fetch(url)
        if int(resp.status) >= 400:
            raise RuntimeError(f"HTTP {resp.status}")
        text = await resp.text()
        reader = csv.DictReader(io.StringIO(str(text).lstrip("\ufeff")))
        all_rows = list(reader)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 35))
        batch_rows = all_rows[offset:offset + limit]
        synced = skipped = 0
        now = utcnow()
        for raw in batch_rows:
            name = pick(raw, "Facility Name", "Facility", "Station Name", "Name")
            if not name:
                skipped += 1
                continue
            country = pick(raw, "Country", "Party", "Nation", "National Antarctic Program Country", "NAP Country")
            programme = pick(raw, "National Antarctic Program", "National Antarctic Programme", "Operator", "Programme", "Program")
            facility_type = pick(raw, "Facility Type", "Type") or "Facility"
            seasonality = pick(raw, "Operational Period", "Seasonality", "Operation", "Operational Status")
            status = pick(raw, "Status", "Facility Status")
            lat = parse_coord(pick(raw, "Latitude", "Latitude DD", "Lat", "Y"))
            lon = parse_coord(pick(raw, "Longitude", "Longitude DD", "Lon", "Lng", "Long", "X"))
            external = pick(raw, "Facility ID", "ID", "Identifier", "Code")
            basis = f"{external}|{name}|{country}|{lat}|{lon}"
            source_key = "comnap:" + hashlib.sha256(basis.encode()).hexdigest()[:24]
            await q_write(env, """INSERT INTO public_facilities(source_key,name,country,programme,facility_type,seasonality,status,latitude,longitude,source,source_url,source_updated_at,raw_json,synced_at)
              VALUES(?,?,?,?,?,?,?,?,?,'COMNAP',?,'November 2024',?,?)
              ON CONFLICT(source_key) DO UPDATE SET name=excluded.name,country=excluded.country,programme=excluded.programme,
              facility_type=excluded.facility_type,seasonality=excluded.seasonality,status=excluded.status,latitude=excluded.latitude,
              longitude=excluded.longitude,raw_json=excluded.raw_json,synced_at=excluded.synced_at""",
              source_key, name, country, programme, facility_type, seasonality, status, lat, lon, url, json.dumps(raw, ensure_ascii=False), now)
            synced += 1
        next_offset = offset + len(batch_rows)
        has_more = next_offset < len(all_rows)
        await source_status(env, "COMNAP Facilities", url, "OK", f"{next_offset}/{len(all_rows)} rows processed; {skipped} skipped in latest batch")
        return {"ok": True, "synced": synced, "skipped": skipped, "processed": next_offset, "total_rows": len(all_rows), "next_offset": next_offset, "has_more": has_more, "source": "COMNAP", "source_url": url}
    except Exception as exc:
        await source_status(env, "COMNAP Facilities", url, "ERROR", str(exc)[:500])
        raise HTTPException(502, f"COMNAP sync failed: {exc}")


@app.get("/api/data-sources")
async def data_sources(request: Request, user=Depends(current_user)):
    env = request.scope["env"]
    rows = await q_all(env, "SELECT * FROM data_sources ORDER BY name")
    facility_count = int(await q_value(env, "SELECT COUNT(*) c FROM public_facilities") or 0)
    live_workers = int(await q_value(env, "SELECT COUNT(*) c FROM personnel WHERE source LIKE 'feed:%'") or 0)
    return {"sources": rows, "facility_count": facility_count, "external_worker_count": live_workers, "worker_feed_configured": bool(env_value(env, "OPERATIONS_FEED_URL"))}


@app.post("/api/public/facilities/sync")
async def sync_public_facilities(request: Request, offset: int = 0, limit: int = 35, user=Depends(require("commander", "logistics"))):
    return await sync_comnap(request.scope["env"], offset, limit)


@app.get("/api/public/facilities")
async def list_public_facilities(request: Request, country: Optional[str] = None, q: Optional[str] = None, limit: int = 500, offset: int = 0, user=Depends(current_user)):
    env = request.scope["env"]
    where, params = [], []
    if country:
        where.append("lower(f.country)=lower(?)"); params.append(country)
    if q:
        where.append("(lower(f.name) LIKE lower(?) OR lower(f.country) LIKE lower(?) OR lower(f.programme) LIKE lower(?))")
        like = f"%{q}%"; params.extend([like, like, like])
    clause = " WHERE " + " AND ".join(where) if where else ""
    limit = max(1, min(limit, 2000)); offset = max(0, offset)
    total = int(await q_value(env, f"SELECT COUNT(*) c FROM public_facilities f{clause}", *params) or 0)
    rows = await q_all(env, f"""SELECT f.*,w.temperature_c,w.wind_speed_kph,w.wind_gusts_kph,w.observed_at weather_observed_at,w.fetched_at weather_fetched_at
      FROM public_facilities f LEFT JOIN facility_weather w ON w.facility_id=f.id{clause}
      ORDER BY f.country,f.name LIMIT ? OFFSET ?""", *(params + [limit, offset]))
    countries = await q_all(env, "SELECT country,COUNT(*) count FROM public_facilities WHERE country<>'' GROUP BY country ORDER BY country")
    return {"items": rows, "total": total, "countries": countries, "source": "COMNAP", "source_url": env_value(env, "COMNAP_FACILITIES_URL", DEFAULT_COMNAP_URL)}


@app.get("/api/public/facilities/{facility_id}/weather")
async def public_facility_weather(facility_id: int, request: Request, force: bool = False, user=Depends(current_user)):
    env = request.scope["env"]
    facility = await q_first(env, "SELECT * FROM public_facilities WHERE id=?", facility_id)
    if not facility: raise HTTPException(404, "Facility not found")
    if facility.get("latitude") is None or facility.get("longitude") is None: raise HTTPException(400, "Facility has no usable coordinates")
    cached = await q_first(env, "SELECT * FROM facility_weather WHERE facility_id=?", facility_id)
    if cached and not force:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
            if age < 600: return {"facility": facility, "weather": cached, "cached": True}
        except Exception: pass
    params = urllib.parse.urlencode({
        "latitude": facility["latitude"], "longitude": facility["longitude"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,snowfall,weather_code,surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "timezone": "UTC", "wind_speed_unit": "kmh", "temperature_unit": "celsius"
    })
    url = env_value(env, "OPEN_METEO_URL", DEFAULT_OPEN_METEO_URL) + "?" + params
    try:
        resp = await cf_fetch(url)
        if int(resp.status) >= 400: raise RuntimeError(f"HTTP {resp.status}")
        payload = to_py(await resp.json()) or {}
        current = payload.get("current") or {}
        weather = {
            "facility_id": facility_id, "temperature_c": current.get("temperature_2m"),
            "apparent_temperature_c": current.get("apparent_temperature"), "relative_humidity": current.get("relative_humidity_2m"),
            "wind_speed_kph": current.get("wind_speed_10m"), "wind_direction_deg": current.get("wind_direction_10m"),
            "wind_gusts_kph": current.get("wind_gusts_10m"), "surface_pressure_hpa": current.get("surface_pressure"),
            "snowfall_mm": current.get("snowfall"), "weather_code": current.get("weather_code"),
            "observed_at": current.get("time"), "source": "Open-Meteo model current conditions", "fetched_at": utcnow(),
        }
        await q_write(env, """INSERT INTO facility_weather(facility_id,temperature_c,apparent_temperature_c,relative_humidity,wind_speed_kph,wind_direction_deg,wind_gusts_kph,surface_pressure_hpa,snowfall_mm,weather_code,observed_at,source,fetched_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(facility_id) DO UPDATE SET temperature_c=excluded.temperature_c,
          apparent_temperature_c=excluded.apparent_temperature_c,relative_humidity=excluded.relative_humidity,wind_speed_kph=excluded.wind_speed_kph,
          wind_direction_deg=excluded.wind_direction_deg,wind_gusts_kph=excluded.wind_gusts_kph,surface_pressure_hpa=excluded.surface_pressure_hpa,
          snowfall_mm=excluded.snowfall_mm,weather_code=excluded.weather_code,observed_at=excluded.observed_at,source=excluded.source,fetched_at=excluded.fetched_at""",
          weather["facility_id"], weather["temperature_c"], weather["apparent_temperature_c"], weather["relative_humidity"], weather["wind_speed_kph"], weather["wind_direction_deg"], weather["wind_gusts_kph"], weather["surface_pressure_hpa"], weather["snowfall_mm"], weather["weather_code"], weather["observed_at"], weather["source"], weather["fetched_at"])
        return {"facility": facility, "weather": weather, "cached": False}
    except Exception as exc:
        raise HTTPException(502, f"Weather refresh failed: {exc}")


@app.post("/api/public/facilities/{facility_id}/import")
async def import_public_facility(facility_id: int, expedition_id: int, request: Request, user=Depends(require("commander", "logistics"))):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    f = await q_first(env, "SELECT * FROM public_facilities WHERE id=?", facility_id)
    if not f: raise HTTPException(404, "Facility not found")
    existing = await q_first(env, "SELECT id FROM locations WHERE expedition_id=? AND name=?", expedition_id, f["name"])
    if existing:
        await q_write(env, "UPDATE locations SET type=?,latitude=?,longitude=?,source='COMNAP' WHERE id=?", f.get("facility_type") or "Station", f.get("latitude"), f.get("longitude"), existing["id"])
        lid, created = existing["id"], False
    else:
        r = await q_write(env, "INSERT INTO locations(expedition_id,name,type,latitude,longitude,source,external_id) VALUES(?,?,?,?,?,'COMNAP',?)", expedition_id, f["name"], f.get("facility_type") or "Station", f.get("latitude"), f.get("longitude"), str(facility_id))
        lid, created = last_row_id(r), True
    await log_activity(env, expedition_id, "location", f"Public facility {f['name']} ({f.get('country') or 'Antarctica'}) {'imported' if created else 'updated'} from COMNAP reference data", user["id"])
    await broadcast(env, expedition_id, "location.public_imported", "location", lid, {"facility_id": facility_id, "name": f["name"]})
    return {"ok": True, "location_id": lid, "created": created}


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
    tables = ["organizations", "users", "expeditions", "locations", "personnel", "cargo", "cargo_events", "inventory_items", "inventory_events", "vehicles", "assets", "incidents", "incident_events", "activity", "telemetry_positions", "public_facilities", "facility_weather", "data_sources"]
    payload = {"exported_at": utcnow(), "version": APP_VERSION}
    org_id = user["organization_id"]
    # Global reference tables are exported; operational tables are restricted by tenant.
    payload["organizations"] = await q_all(env, "SELECT * FROM organizations WHERE id=?", org_id)
    payload["users"] = await q_all(env, "SELECT id,organization_id,email,name,role,active,created_at FROM users WHERE organization_id=?", org_id)
    exp_ids = [x["id"] for x in await q_all(env, "SELECT id FROM expeditions WHERE organization_id=?", org_id)]
    payload["expeditions"] = await q_all(env, "SELECT * FROM expeditions WHERE organization_id=?", org_id)
    for table in ["locations", "personnel", "cargo", "inventory_items", "vehicles", "assets", "incidents", "activity", "telemetry_positions"]:
        if exp_ids:
            placeholders = ",".join("?" for _ in exp_ids)
            payload[table] = await q_all(env, f"SELECT * FROM {table} WHERE expedition_id IN ({placeholders})", *exp_ids)
        else:
            payload[table] = []
    payload["public_facilities"] = await q_all(env, "SELECT * FROM public_facilities")
    payload["facility_weather"] = await q_all(env, "SELECT * FROM facility_weather")
    payload["data_sources"] = await q_all(env, "SELECT * FROM data_sources")
    raw = json.dumps(payload, separators=(",", ":"), default=str)
    try:
        key = f"backups/org-{org_id}/polarops-{datetime.now(timezone.utc).date()}.json"
        await env.BACKUPS.put(key, raw)
        payload["r2_backup_key"] = key
    except Exception:
        pass
    return payload


@app.get("/api/health")
async def health(request: Request):
    env = request.scope["env"]
    count = int(await q_value(env, "SELECT COUNT(*) c FROM public_facilities") or 0)
    return {"ok": True, "time": utcnow(), "database": "Cloudflare D1", "realtime": True, "durable_objects": True, "public_facilities": count, "worker_feed_configured": bool(env_value(env, "OPERATIONS_FEED_URL")), "version": APP_VERSION}


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


# ----------------------------- Durable Object -----------------------------

class ExpeditionRoom(DurableObject):
    """One hibernatable WebSocket room per expedition."""

    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.sessions = {}
        try:
            for ws in self.ctx.getWebSockets():
                attachment = ws.deserializeAttachment()
                self.sessions[str(attachment or "unauth")] = ws
        except Exception:
            pass

    async def fetch(self, request):
        upgrade = request.headers.get("Upgrade")
        if not upgrade or str(upgrade).lower() != "websocket":
            return Response("Expected WebSocket upgrade", status=426)
        client, server = WebSocketPair.new().object_values()
        self.ctx.acceptWebSocket(server)
        sid = "unauth:" + str(uuid.uuid4())
        server.serializeAttachment(sid)
        self.sessions[sid] = server
        return Response(None, status=101, web_socket=client)

    async def webSocketMessage(self, ws, message):
        try:
            data = json.loads(str(message))
        except Exception:
            return
        attachment = str(ws.deserializeAttachment() or "")
        if data.get("type") == "auth":
            try:
                payload = decode_token(str(data.get("token") or ""), env_value(self.env, "AUTH_SECRET", "dev-only-change-me"))
                new_attachment = f"auth:{payload['uid']}:{payload['oid']}:{payload['role']}"
                self.sessions.pop(attachment, None)
                ws.serializeAttachment(new_attachment)
                self.sessions[new_attachment] = ws
                ws.send(json.dumps({"type": "auth.ok", "expedition_id": int(self.ctx.id.name or 0), "user_id": payload["uid"], "server_time": utcnow()}))
            except Exception:
                ws.send(json.dumps({"type": "auth.error", "detail": "Invalid or expired session"}))
                ws.close(4401, "Unauthorized")
            return
        if not attachment.startswith("auth:"):
            ws.send(json.dumps({"type": "auth.error", "detail": "Authentication required"}))
            return
        if data.get("type") == "ping":
            ws.send(json.dumps({"type": "pong", "server_time": utcnow()}))

    async def webSocketClose(self, ws, code, reason, wasClean):
        attachment = str(ws.deserializeAttachment() or "")
        self.sessions.pop(attachment, None)
        try:
            ws.close(code, reason)
        except Exception:
            pass

    async def broadcast_json(self, payload: str):
        # Use ctx.getWebSockets() so hibernated/reconstructed objects still reach all clients.
        sent = 0
        for ws in self.ctx.getWebSockets():
            try:
                attachment = str(ws.deserializeAttachment() or "")
                if attachment.startswith("auth:"):
                    ws.send(payload)
                    sent += 1
            except Exception:
                pass
        return sent


# ----------------------------- Worker entrypoint ---------------------------

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path = urllib.parse.urlparse(request.url).path
        if path.startswith("/ws/expeditions/"):
            parts = [p for p in path.split("/") if p]
            if len(parts) != 3:
                return Response("Invalid WebSocket path", status=404)
            expedition_id = parts[-1]
            stub = self.env.EXPEDITION_ROOM.getByName(expedition_id)
            return await stub.fetch(request)
        return await asgi.fetch(app, request, self.env)
