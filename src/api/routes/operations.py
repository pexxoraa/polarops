from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.dependencies import current_user, ensure_expedition_access, require_permission
from database.d1 import last_row_id, q_all, q_batch, q_first, q_value, q_write
from repositories.activity import log_activity


router = APIRouter()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def access(request: Request, user: dict, expedition_id: int):
    env = request.scope["env"]
    expedition = await ensure_expedition_access(env, user, expedition_id)
    return env, expedition


async def audit(env, user: dict, expedition_id: int | None, action: str, entity_type: str, entity_id: int | None, detail: Any = None):
    await q_write(
        env,
        """INSERT INTO audit_events(organization_id,expedition_id,user_id,action,entity_type,entity_id,detail_json,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        int(user["organization_id"]),
        expedition_id,
        int(user["id"]),
        action,
        entity_type,
        entity_id,
        json.dumps(detail or {}, separators=(",", ":")),
        now_iso(),
    )


def require_fields(payload: dict, *names: str):
    missing = [name for name in names if payload.get(name) in (None, "")]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required field(s): {', '.join(missing)}")


def validate_coordinates(latitude, longitude, label: str = "Coordinates"):
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{label} must be numeric")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise HTTPException(status_code=400, detail=f"{label} are outside valid latitude/longitude ranges")
    return lat, lon


async def ensure_entity_in_expedition(env, table: str, item_id, expedition_id: int, label: str):
    if item_id in (None, ""):
        return
    row = await q_first(env, f"SELECT id FROM {table} WHERE id=? AND expedition_id=?", int(item_id), expedition_id)
    if not row:
        raise HTTPException(status_code=400, detail=f"{label} does not belong to this expedition")


async def synthesized_alerts(env, expedition_id: int) -> list[dict]:
    result = []
    overdue_sql = """SELECT p.id,p.name,l.name location_name,p.last_checkin FROM personnel p
           LEFT JOIN locations l ON l.id=p.location_id
           WHERE p.expedition_id=? AND (
             p.status='Overdue' OR
             (p.last_checkin IS NOT NULL AND datetime(p.last_checkin) < datetime('now','-12 hours'))
           ) ORDER BY p.name"""
    low_stock_sql = """SELECT id,name,quantity,min_quantity,unit FROM inventory_items
           WHERE expedition_id=? AND quantity < min_quantity ORDER BY name"""
    vehicles_sql = """SELECT id,code,name,status,fuel_percent FROM vehicles
           WHERE expedition_id=? AND (status!='Operational' OR fuel_percent < 30) ORDER BY code"""
    checkins_sql = """SELECT id,team_name,channel,expected_at FROM comms_checkins
           WHERE expedition_id=? AND status='Expected' AND datetime(expected_at) < datetime('now') ORDER BY expected_at"""
    fences_sql = """SELECT * FROM geofences
           WHERE expedition_id=? AND active=1
             AND kind IN ('Safe zone','Restricted area','Hazard zone')
           ORDER BY id"""
    overdue, low_stock, vehicles, checkins, fences = await q_batch(env, [
        (overdue_sql, (expedition_id,)),
        (low_stock_sql, (expedition_id,)),
        (vehicles_sql, (expedition_id,)),
        (checkins_sql, (expedition_id,)),
        (fences_sql, (expedition_id,)),
    ])
    for row in overdue:
        result.append({
            "id": f"personnel-{row['id']}", "generated": True, "severity": "Critical",
            "source": "Personnel", "title": f"{row['name']} check-in overdue",
            "detail": f"Last known at {row.get('location_name') or 'unknown location'}",
            "status": "Open", "entity_type": "personnel", "entity_id": row["id"],
        })

    for row in low_stock:
        result.append({
            "id": f"inventory-{row['id']}", "generated": True, "severity": "Warning",
            "source": "Inventory", "title": f"{row['name']} below safety stock",
            "detail": f"{row['quantity']} {row['unit']} available; minimum {row['min_quantity']} {row['unit']}",
            "status": "Open", "entity_type": "inventory", "entity_id": row["id"],
        })

    for row in vehicles:
        severity = "Critical" if float(row.get("fuel_percent") or 100) < 15 else "Warning"
        result.append({
            "id": f"vehicle-{row['id']}", "generated": True, "severity": severity,
            "source": "Vehicle", "title": f"{row['code']} requires attention",
            "detail": f"Status {row.get('status')}; fuel {row.get('fuel_percent')}%",
            "status": "Open", "entity_type": "vehicle", "entity_id": row["id"],
        })

    for row in checkins:
        result.append({
            "id": f"comms-{row['id']}", "generated": True, "severity": "Critical",
            "source": "Communications", "title": f"{row['team_name']} communications overdue",
            "detail": f"Expected via {row['channel']} at {row['expected_at']}",
            "status": "Open", "entity_type": "comms", "entity_id": row["id"],
        })

    if fences:
        positions_sql = """SELECT t.* FROM telemetry_positions t
               JOIN (
                 SELECT entity_type,entity_id,MAX(id) max_id
                 FROM telemetry_positions
                 WHERE expedition_id=? AND entity_type IN ('personnel','vehicle')
                 GROUP BY entity_type,entity_id
               ) latest ON latest.max_id=t.id
               WHERE datetime(t.recorded_at) >= datetime('now','-24 hours')"""
        positions, people_rows, vehicle_rows = await q_batch(env, [
            (positions_sql, (expedition_id,)),
            ("SELECT id,name FROM personnel WHERE expedition_id=?", (expedition_id,)),
            ("SELECT id,code,name FROM vehicles WHERE expedition_id=?", (expedition_id,)),
        ])
        people = {int(r["id"]): r["name"] for r in people_rows}
        vehicles_by_id = {int(r["id"]): f"{r['code']} · {r['name']}" for r in vehicle_rows}
        for pos in positions:
            entity_type = str(pos.get("entity_type") or "")
            entity_id = int(pos["entity_id"])
            label = people.get(entity_id, f"Personnel {entity_id}") if entity_type == "personnel" else vehicles_by_id.get(entity_id, f"Vehicle {entity_id}")
            for fence in fences:
                distance_km = haversine_km(pos.get("latitude"), pos.get("longitude"), fence.get("center_lat"), fence.get("center_lon"))
                if distance_km is None:
                    continue
                distance_m = distance_km * 1000.0
                inside = distance_m <= float(fence["radius_m"])
                kind = str(fence.get("kind") or "")
                breached = (kind == "Safe zone" and not inside) or (kind in ("Restricted area", "Hazard zone") and inside)
                if not breached:
                    continue
                if kind == "Safe zone":
                    detail = f"{label} is {round(distance_m)} m from {fence['name']} center and outside the {round(float(fence['radius_m']))} m approved zone."
                else:
                    detail = f"{label} is inside {fence['name']} ({kind.lower()}); distance to zone center {round(distance_m)} m."
                result.append({
                    "id": f"geofence-{fence['id']}-{entity_type}-{entity_id}",
                    "generated": True,
                    "severity": fence.get("severity") or "Warning",
                    "source": "Geofence",
                    "title": f"{label} · {fence['name']}",
                    "detail": detail,
                    "status": "Open",
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                })
    return result


@router.get("/api/ops/summary")
async def operations_summary(request: Request, expedition_id: int, user=Depends(require_permission("operations.read"))):
    env, _ = await access(request, user, expedition_id)
    now = now_iso()
    tasks, routes, geofences, handovers, manual_alerts, comms, readiness = await q_batch(env, [
        ("SELECT * FROM mission_tasks WHERE expedition_id=? ORDER BY COALESCE(start_at,due_at,created_at) DESC LIMIT 100", (expedition_id,)),
        ("SELECT * FROM planned_routes WHERE expedition_id=? ORDER BY created_at DESC LIMIT 50", (expedition_id,)),
        ("SELECT * FROM geofences WHERE expedition_id=? ORDER BY active DESC,name", (expedition_id,)),
        ("SELECT h.*,u.name author_name FROM shift_handovers h LEFT JOIN users u ON u.id=h.author_user_id WHERE h.expedition_id=? ORDER BY h.created_at DESC LIMIT 10", (expedition_id,)),
        ("SELECT * FROM ops_alerts WHERE expedition_id=? AND status!='Resolved' ORDER BY created_at DESC", (expedition_id,)),
        ("SELECT * FROM comms_checkins WHERE expedition_id=? ORDER BY expected_at DESC LIMIT 50", (expedition_id,)),
        ("SELECT * FROM readiness_items WHERE expedition_id=? ORDER BY category,label", (expedition_id,)),
    ])
    generated = await synthesized_alerts(env, expedition_id)
    complete = sum(1 for r in readiness if r.get("status") == "Complete")
    return {
        "now": now,
        "tasks": tasks,
        "routes": routes,
        "geofences": geofences,
        "alerts": generated + manual_alerts,
        "handovers": handovers,
        "comms": comms,
        "readiness": readiness,
        "readiness_summary": {"complete": complete, "total": len(readiness)},
    }


@router.get("/api/ops/tasks")
async def list_tasks(request: Request, expedition_id: int, user=Depends(require_permission("operations.read"))):
    env, _ = await access(request, user, expedition_id)
    return {"items": await q_all(env, "SELECT * FROM mission_tasks WHERE expedition_id=? ORDER BY COALESCE(start_at,due_at,created_at),id", expedition_id)}


@router.post("/api/ops/tasks")
async def create_task(request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    require_fields(payload, "expedition_id", "title")
    expedition_id = int(payload["expedition_id"])
    env, _ = await access(request, user, expedition_id)
    await ensure_entity_in_expedition(env, "locations", payload.get("location_id"), expedition_id, "Location")
    ts = now_iso()
    result = await q_write(
        env,
        """INSERT INTO mission_tasks(expedition_id,title,category,status,priority,start_at,due_at,assigned_to,location_id,notes,created_by,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        expedition_id, payload["title"], payload.get("category") or "Operations", payload.get("status") or "Planned",
        payload.get("priority") or "Normal", payload.get("start_at"), payload.get("due_at"), payload.get("assigned_to"),
        payload.get("location_id"), payload.get("notes"), int(user["id"]), ts, ts,
    )
    item_id = last_row_id(result)
    await audit(env, user, expedition_id, "created", "mission_task", item_id, payload)
    await log_activity(env, expedition_id, "task.created", f"Mission task created: {payload['title']}", int(user["id"]))
    return await q_first(env, "SELECT * FROM mission_tasks WHERE id=?", item_id)


@router.patch("/api/ops/tasks/{item_id}")
async def update_task(item_id: int, request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    row = await q_first(request.scope["env"], "SELECT * FROM mission_tasks WHERE id=?", item_id)
    if not row:
        raise HTTPException(404, "Task not found")
    env, _ = await access(request, user, int(row["expedition_id"]))
    allowed = ["title","category","status","priority","start_at","due_at","assigned_to","location_id","notes"]
    data = {k: payload[k] for k in allowed if k in payload}
    if "location_id" in data:
        await ensure_entity_in_expedition(env, "locations", data.get("location_id"), int(row["expedition_id"]), "Location")
    if data:
        data["updated_at"] = now_iso()
        await q_write(env, f"UPDATE mission_tasks SET {','.join(f'{k}=?' for k in data)} WHERE id=?", *data.values(), item_id)
        await audit(env, user, int(row["expedition_id"]), "updated", "mission_task", item_id, data)
    return await q_first(env, "SELECT * FROM mission_tasks WHERE id=?", item_id)


@router.get("/api/ops/routes")
async def list_routes(request: Request, expedition_id: int, user=Depends(require_permission("operations.read"))):
    env, _ = await access(request, user, expedition_id)
    return {"routes": await q_all(env, "SELECT * FROM planned_routes WHERE expedition_id=? ORDER BY created_at DESC", expedition_id),
            "geofences": await q_all(env, "SELECT * FROM geofences WHERE expedition_id=? ORDER BY active DESC,name", expedition_id)}


@router.post("/api/ops/routes")
async def create_route(request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    require_fields(payload, "expedition_id", "name", "start_lat", "start_lon", "end_lat", "end_lon")
    expedition_id = int(payload["expedition_id"])
    env, _ = await access(request, user, expedition_id)
    await ensure_entity_in_expedition(env, "vehicles", payload.get("vehicle_id"), expedition_id, "Vehicle")
    await ensure_entity_in_expedition(env, "personnel", payload.get("personnel_id"), expedition_id, "Personnel")
    start_lat, start_lon = validate_coordinates(payload["start_lat"], payload["start_lon"], "Route start")
    end_lat, end_lon = validate_coordinates(payload["end_lat"], payload["end_lon"], "Route destination")
    distance = haversine_km(start_lat, start_lon, end_lat, end_lon)
    eta = int(payload.get("eta_minutes") or max(10, distance / max(float(payload.get("speed_kph") or 20), 1) * 60))
    fuel = float(payload.get("fuel_liters") or round(distance * float(payload.get("liters_per_km") or 0.45), 1))
    risk_bits = []
    if distance > 100:
        risk_bits.append("Long-distance traverse")
    if eta > 360:
        risk_bits.append("Extended field exposure")
    risk = "; ".join(risk_bits) or "No automatic route threshold exception"
    ts = now_iso()
    result = await q_write(
        env,
        """INSERT INTO planned_routes(expedition_id,name,start_lat,start_lon,end_lat,end_lon,waypoints_json,distance_km,eta_minutes,fuel_liters,vehicle_id,personnel_id,status,risk_summary,created_by,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        expedition_id, payload["name"], start_lat, start_lon, end_lat, end_lon,
        json.dumps(payload.get("waypoints") or []), distance, eta, fuel, payload.get("vehicle_id"), payload.get("personnel_id"),
        payload.get("status") or "Planned", risk, int(user["id"]), ts, ts,
    )
    item_id = last_row_id(result)
    await audit(env, user, expedition_id, "created", "planned_route", item_id, {"distance_km": distance, **payload})
    return await q_first(env, "SELECT * FROM planned_routes WHERE id=?", item_id)


@router.post("/api/ops/geofences")
async def create_geofence(request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    require_fields(payload, "expedition_id", "name", "center_lat", "center_lon", "radius_m")
    expedition_id = int(payload["expedition_id"])
    env, _ = await access(request, user, expedition_id)
    center_lat, center_lon = validate_coordinates(payload["center_lat"], payload["center_lon"], "Geofence center")
    try:
        radius_m = float(payload["radius_m"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Geofence radius must be numeric")
    if radius_m <= 0:
        raise HTTPException(status_code=400, detail="Geofence radius must be greater than zero")
    ts = now_iso()
    result = await q_write(
        env,
        """INSERT INTO geofences(expedition_id,name,kind,center_lat,center_lon,radius_m,severity,active,notes,created_by,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        expedition_id, payload["name"], payload.get("kind") or "Safe zone", center_lat, center_lon,
        radius_m, payload.get("severity") or "Warning", 1 if payload.get("active", True) else 0,
        payload.get("notes"), int(user["id"]), ts, ts,
    )
    item_id = last_row_id(result)
    await audit(env, user, expedition_id, "created", "geofence", item_id, payload)
    return await q_first(env, "SELECT * FROM geofences WHERE id=?", item_id)


@router.get("/api/ops/alerts")
async def list_alerts(request: Request, expedition_id: int, user=Depends(require_permission("operations.read"))):
    env, _ = await access(request, user, expedition_id)
    manual = await q_all(env, "SELECT * FROM ops_alerts WHERE expedition_id=? ORDER BY CASE severity WHEN 'Critical' THEN 0 WHEN 'Warning' THEN 1 ELSE 2 END,created_at DESC", expedition_id)
    return {"items": await synthesized_alerts(env, expedition_id) + manual}


@router.post("/api/ops/alerts")
async def create_alert(request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    require_fields(payload, "expedition_id", "title")
    expedition_id = int(payload["expedition_id"])
    env, _ = await access(request, user, expedition_id)
    ts = now_iso()
    result = await q_write(
        env,
        """INSERT INTO ops_alerts(expedition_id,severity,source,title,detail,status,assigned_to,entity_type,entity_id,created_by,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        expedition_id, payload.get("severity") or "Advisory", payload.get("source") or "Manual", payload["title"], payload.get("detail"),
        "Open", payload.get("assigned_to"), payload.get("entity_type"), payload.get("entity_id"), int(user["id"]), ts,
    )
    item_id = last_row_id(result)
    await audit(env, user, expedition_id, "created", "alert", item_id, payload)
    return await q_first(env, "SELECT * FROM ops_alerts WHERE id=?", item_id)


@router.patch("/api/ops/alerts/{item_id}")
async def update_alert(item_id: int, request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT * FROM ops_alerts WHERE id=?", item_id)
    if not row:
        raise HTTPException(404, "Alert not found")
    await access(request, user, int(row["expedition_id"]))
    status = payload.get("status")
    assigned = payload.get("assigned_to", row.get("assigned_to"))
    ack = now_iso() if status == "Acknowledged" else row.get("acknowledged_at")
    resolved = now_iso() if status == "Resolved" else row.get("resolved_at")
    await q_write(env, "UPDATE ops_alerts SET status=?,assigned_to=?,acknowledged_at=?,resolved_at=? WHERE id=?", status or row["status"], assigned, ack, resolved, item_id)
    await audit(env, user, int(row["expedition_id"]), "updated", "alert", item_id, payload)
    return await q_first(env, "SELECT * FROM ops_alerts WHERE id=?", item_id)


@router.get("/api/ops/science")
async def list_science(request: Request, expedition_id: int, user=Depends(require_permission("operations.read"))):
    env, _ = await access(request, user, expedition_id)
    return {"items": await q_all(env, "SELECT s.*,p.name researcher_name FROM science_records s LEFT JOIN personnel p ON p.id=s.researcher_id WHERE s.expedition_id=? ORDER BY COALESCE(s.collected_at,s.created_at) DESC", expedition_id)}


@router.post("/api/ops/science")
async def create_science(request: Request, payload: dict, user=Depends(require_permission("science.manage"))):
    require_fields(payload, "expedition_id", "project", "title")
    expedition_id = int(payload["expedition_id"])
    env, _ = await access(request, user, expedition_id)
    await ensure_entity_in_expedition(env, "personnel", payload.get("researcher_id"), expedition_id, "Researcher")
    latitude, longitude = payload.get("latitude"), payload.get("longitude")
    if latitude not in (None, "") or longitude not in (None, ""):
        if latitude in (None, "") or longitude in (None, ""):
            raise HTTPException(status_code=400, detail="Science coordinates require both latitude and longitude")
        latitude, longitude = validate_coordinates(latitude, longitude, "Science record coordinates")
    result = await q_write(
        env,
        """INSERT INTO science_records(expedition_id,project,sample_id,record_type,title,latitude,longitude,collected_at,researcher_id,storage_location,notes,created_by,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        expedition_id, payload["project"], payload.get("sample_id"), payload.get("record_type") or "Observation", payload["title"],
        latitude, longitude, payload.get("collected_at") or now_iso(), payload.get("researcher_id"),
        payload.get("storage_location"), payload.get("notes"), int(user["id"]), now_iso(),
    )
    item_id = last_row_id(result)
    await audit(env, user, expedition_id, "created", "science_record", item_id, payload)
    return await q_first(env, "SELECT * FROM science_records WHERE id=?", item_id)


@router.get("/api/ops/comms")
async def list_comms(request: Request, expedition_id: int, user=Depends(require_permission("operations.read"))):
    env, _ = await access(request, user, expedition_id)
    return {"items": await q_all(env, "SELECT * FROM comms_checkins WHERE expedition_id=? ORDER BY expected_at DESC", expedition_id)}


@router.post("/api/ops/comms")
async def create_comms(request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    require_fields(payload, "expedition_id", "team_name", "expected_at")
    expedition_id = int(payload["expedition_id"])
    env, _ = await access(request, user, expedition_id)
    ts = now_iso()
    result = await q_write(env, """INSERT INTO comms_checkins(expedition_id,team_name,channel,expected_at,status,notes,created_by,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)""", expedition_id, payload["team_name"], payload.get("channel") or "Satellite", payload["expected_at"], "Expected", payload.get("notes"), int(user["id"]), ts, ts)
    item_id = last_row_id(result)
    await audit(env, user, expedition_id, "created", "comms_checkin", item_id, payload)
    return await q_first(env, "SELECT * FROM comms_checkins WHERE id=?", item_id)


@router.patch("/api/ops/comms/{item_id}")
async def update_comms(item_id: int, request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT * FROM comms_checkins WHERE id=?", item_id)
    if not row:
        raise HTTPException(404, "Communications check-in not found")
    await access(request, user, int(row["expedition_id"]))
    status = payload.get("status") or row["status"]
    actual = payload.get("actual_at") or (now_iso() if status == "Completed" else row.get("actual_at"))
    await q_write(env, "UPDATE comms_checkins SET status=?,actual_at=?,notes=?,updated_at=? WHERE id=?", status, actual, payload.get("notes", row.get("notes")), now_iso(), item_id)
    await audit(env, user, int(row["expedition_id"]), "updated", "comms_checkin", item_id, payload)
    return await q_first(env, "SELECT * FROM comms_checkins WHERE id=?", item_id)


@router.get("/api/ops/readiness")
async def list_readiness(request: Request, expedition_id: int, user=Depends(require_permission("operations.read"))):
    env, _ = await access(request, user, expedition_id)
    rows = await q_all(env, "SELECT * FROM readiness_items WHERE expedition_id=? ORDER BY category,label", expedition_id)
    return {"items": rows, "complete": sum(1 for r in rows if r.get("status") == "Complete"), "total": len(rows)}


@router.post("/api/ops/readiness")
async def create_readiness(request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    require_fields(payload, "expedition_id", "category", "label")
    expedition_id = int(payload["expedition_id"])
    env, _ = await access(request, user, expedition_id)
    ts = now_iso()
    result = await q_write(env, """INSERT INTO readiness_items(expedition_id,category,label,status,owner,due_at,notes,updated_by,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""", expedition_id, payload["category"], payload["label"], payload.get("status") or "Pending", payload.get("owner"), payload.get("due_at"), payload.get("notes"), int(user["id"]), ts, ts)
    item_id = last_row_id(result)
    await audit(env, user, expedition_id, "created", "readiness_item", item_id, payload)
    return await q_first(env, "SELECT * FROM readiness_items WHERE id=?", item_id)


@router.patch("/api/ops/readiness/{item_id}")
async def update_readiness(item_id: int, request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    env = request.scope["env"]
    row = await q_first(env, "SELECT * FROM readiness_items WHERE id=?", item_id)
    if not row:
        raise HTTPException(404, "Readiness item not found")
    await access(request, user, int(row["expedition_id"]))
    status = payload.get("status") or row["status"]
    await q_write(env, "UPDATE readiness_items SET status=?,owner=?,notes=?,updated_by=?,updated_at=? WHERE id=?", status, payload.get("owner", row.get("owner")), payload.get("notes", row.get("notes")), int(user["id"]), now_iso(), item_id)
    await audit(env, user, int(row["expedition_id"]), "updated", "readiness_item", item_id, payload)
    return await q_first(env, "SELECT * FROM readiness_items WHERE id=?", item_id)


@router.get("/api/ops/incident-command/{incident_id}")
async def incident_command(incident_id: int, request: Request, user=Depends(require_permission("operations.read"))):
    env = request.scope["env"]
    incident = await q_first(env, "SELECT * FROM incidents WHERE id=?", incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")
    await access(request, user, int(incident["expedition_id"]))
    actions = await q_all(env, "SELECT * FROM incident_actions WHERE incident_id=? ORDER BY created_at", incident_id)
    events = await q_all(env, "SELECT * FROM incident_events WHERE incident_id=? ORDER BY created_at", incident_id)
    return {"incident": incident, "actions": actions, "events": events}


@router.post("/api/ops/incident-command/{incident_id}/actions")
async def create_incident_action(incident_id: int, request: Request, payload: dict, user=Depends(require_permission("incident.manage"))):
    require_fields(payload, "task")
    env = request.scope["env"]
    incident = await q_first(env, "SELECT * FROM incidents WHERE id=?", incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")
    await access(request, user, int(incident["expedition_id"]))
    ts = now_iso()
    result = await q_write(env, "INSERT INTO incident_actions(incident_id,task,owner,status,due_at,notes,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           incident_id, payload["task"], payload.get("owner"), payload.get("status") or "Open", payload.get("due_at"), payload.get("notes"), int(user["id"]), ts, ts)
    item_id = last_row_id(result)
    await audit(env, user, int(incident["expedition_id"]), "created", "incident_action", item_id, payload)
    return await q_first(env, "SELECT * FROM incident_actions WHERE id=?", item_id)


@router.patch("/api/ops/incident-command/actions/{item_id}")
async def update_incident_action(item_id: int, request: Request, payload: dict, user=Depends(require_permission("incident.manage"))):
    env = request.scope["env"]
    row = await q_first(env, """SELECT a.*,i.expedition_id FROM incident_actions a
        JOIN incidents i ON i.id=a.incident_id WHERE a.id=?""", item_id)
    if not row:
        raise HTTPException(404, "Incident action not found")
    expedition_id = int(row["expedition_id"])
    await access(request, user, expedition_id)
    status = payload.get("status") or row["status"]
    owner = payload.get("owner", row.get("owner"))
    notes = payload.get("notes", row.get("notes"))
    await q_write(env, "UPDATE incident_actions SET status=?,owner=?,notes=?,updated_at=? WHERE id=?", status, owner, notes, now_iso(), item_id)
    await audit(env, user, expedition_id, "updated", "incident_action", item_id, payload)
    return await q_first(env, "SELECT * FROM incident_actions WHERE id=?", item_id)


@router.get("/api/ops/handovers")
async def list_handovers(request: Request, expedition_id: int, user=Depends(require_permission("operations.read"))):
    env, _ = await access(request, user, expedition_id)
    return {"items": await q_all(env, "SELECT h.*,u.name author_name FROM shift_handovers h LEFT JOIN users u ON u.id=h.author_user_id WHERE h.expedition_id=? ORDER BY h.created_at DESC", expedition_id)}


@router.post("/api/ops/handovers")
async def create_handover(request: Request, payload: dict, user=Depends(require_permission("operations.manage"))):
    require_fields(payload, "expedition_id", "shift_name", "summary")
    expedition_id = int(payload["expedition_id"])
    env, _ = await access(request, user, expedition_id)
    result = await q_write(env, """INSERT INTO shift_handovers(expedition_id,shift_name,author_user_id,summary,unresolved_alerts,deployed_teams,vehicle_issues,weather_notes,cargo_priorities,science_ops,next_tasks,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", expedition_id, payload["shift_name"], int(user["id"]), payload["summary"], payload.get("unresolved_alerts"), payload.get("deployed_teams"), payload.get("vehicle_issues"), payload.get("weather_notes"), payload.get("cargo_priorities"), payload.get("science_ops"), payload.get("next_tasks"), now_iso())
    item_id = last_row_id(result)
    await audit(env, user, expedition_id, "created", "shift_handover", item_id, payload)
    return await q_first(env, "SELECT * FROM shift_handovers WHERE id=?", item_id)


@router.get("/api/ops/sitrep")
async def sitrep(request: Request, expedition_id: int, user=Depends(require_permission("operations.read"))):
    env, expedition = await access(request, user, expedition_id)
    personnel_total = await q_value(env, "SELECT COUNT(*) c FROM personnel WHERE expedition_id=?", expedition_id) or 0
    personnel_overdue = await q_value(env, """SELECT COUNT(*) c FROM personnel WHERE expedition_id=? AND (
        status='Overdue' OR (last_checkin IS NOT NULL AND datetime(last_checkin) < datetime('now','-12 hours'))
    )""", expedition_id) or 0
    deployed = await q_value(env, "SELECT COUNT(*) c FROM personnel WHERE expedition_id=? AND status IN ('Moving','Deployed')", expedition_id) or 0
    vehicles_total = await q_value(env, "SELECT COUNT(*) c FROM vehicles WHERE expedition_id=?", expedition_id) or 0
    vehicles_ok = await q_value(env, "SELECT COUNT(*) c FROM vehicles WHERE expedition_id=? AND status='Operational'", expedition_id) or 0
    inventory_warn = await q_value(env, "SELECT COUNT(*) c FROM inventory_items WHERE expedition_id=? AND quantity<min_quantity", expedition_id) or 0
    incidents = await q_value(env, "SELECT COUNT(*) c FROM incidents WHERE expedition_id=? AND status!='Resolved'", expedition_id) or 0
    cargo_transit = await q_value(env, "SELECT COUNT(*) c FROM cargo WHERE expedition_id=? AND status NOT IN ('Delivered','Cancelled')", expedition_id) or 0
    alerts = await synthesized_alerts(env, expedition_id)
    readiness_total = await q_value(env, "SELECT COUNT(*) c FROM readiness_items WHERE expedition_id=?", expedition_id) or 0
    readiness_complete = await q_value(env, "SELECT COUNT(*) c FROM readiness_items WHERE expedition_id=? AND status='Complete'", expedition_id) or 0
    return {
        "generated_at": now_iso(), "expedition": expedition,
        "personnel": {"total": personnel_total, "overdue": personnel_overdue, "deployed": deployed},
        "vehicles": {"total": vehicles_total, "operational": vehicles_ok},
        "inventory_warnings": inventory_warn, "active_incidents": incidents, "cargo_in_transit": cargo_transit,
        "alerts": alerts[:10], "readiness": {"complete": readiness_complete, "total": readiness_total},
    }


@router.get("/api/ops/audit")
async def audit_trail(request: Request, expedition_id: int, limit: int = 200, user=Depends(require_permission("audit.read"))):
    env, _ = await access(request, user, expedition_id)
    limit = max(1, min(limit, 500))
    explicit = await q_all(env, """SELECT a.*,u.name user_name FROM audit_events a LEFT JOIN users u ON u.id=a.user_id
        WHERE a.expedition_id=? ORDER BY a.created_at DESC LIMIT ?""", expedition_id, limit)
    activity = await q_all(env, """SELECT a.*,u.name user_name FROM activity a LEFT JOIN users u ON u.id=a.user_id
        WHERE a.expedition_id=? ORDER BY a.created_at DESC LIMIT ?""", expedition_id, limit)
    merged = [{"kind": "audit", **x} for x in explicit] + [{"kind": "activity", **x} for x in activity]
    merged.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"items": merged[:limit]}


@router.get("/api/ops/search")
async def global_search(request: Request, expedition_id: int, q: str, user=Depends(require_permission("operations.read"))):
    env, _ = await access(request, user, expedition_id)
    term = f"%{q.strip()}%"
    if len(q.strip()) < 2:
        return {"items": []}
    specs = [
        ("personnel", "SELECT id,name title,role detail FROM personnel WHERE expedition_id=? AND (name LIKE ? OR role LIKE ?) LIMIT 8"),
        ("cargo", "SELECT id,code title,name detail FROM cargo WHERE expedition_id=? AND (code LIKE ? OR name LIKE ?) LIMIT 8"),
        ("inventory", "SELECT id,name title,sku detail FROM inventory_items WHERE expedition_id=? AND (name LIKE ? OR sku LIKE ?) LIMIT 8"),
        ("vehicle", "SELECT id,code title,name detail FROM vehicles WHERE expedition_id=? AND (code LIKE ? OR name LIKE ?) LIMIT 8"),
        ("asset", "SELECT id,code title,name detail FROM assets WHERE expedition_id=? AND (code LIKE ? OR name LIKE ? OR category LIKE ?) LIMIT 8"),
        ("location", "SELECT id,name title,type detail FROM locations WHERE expedition_id=? AND (name LIKE ? OR type LIKE ?) LIMIT 8"),
        ("incident", "SELECT id,title,status detail FROM incidents WHERE expedition_id=? AND (title LIKE ? OR description LIKE ?) LIMIT 8"),
        ("task", "SELECT id,title,status detail FROM mission_tasks WHERE expedition_id=? AND (title LIKE ? OR notes LIKE ?) LIMIT 8"),
        ("route", "SELECT id,name title,risk_summary detail FROM planned_routes WHERE expedition_id=? AND (name LIKE ? OR risk_summary LIKE ?) LIMIT 8"),
        ("science", "SELECT id,title,project detail FROM science_records WHERE expedition_id=? AND (title LIKE ? OR project LIKE ? OR sample_id LIKE ?) LIMIT 8"),
        ("comms", "SELECT id,team_name title,channel detail FROM comms_checkins WHERE expedition_id=? AND (team_name LIKE ? OR channel LIKE ? OR notes LIKE ?) LIMIT 8"),
        ("handover", "SELECT id,shift_name title,summary detail FROM shift_handovers WHERE expedition_id=? AND (shift_name LIKE ? OR summary LIKE ? OR next_tasks LIKE ?) LIMIT 8"),
        ("facility", "SELECT id,name title,country detail FROM public_facilities WHERE name LIKE ? OR country LIKE ? LIMIT 8"),
        ("arctic_station", "SELECT id,name title,location detail FROM arctic_research_stations WHERE name LIKE ? OR location LIKE ? LIMIT 8"),
    ]
    statements = []
    for _, sql in specs:
        params = []
        if "expedition_id=?" in sql:
            params.append(expedition_id)
        params.extend([term] * sql.count("LIKE ?"))
        statements.append((sql, tuple(params)))
    groups = await q_batch(env, statements)
    items = []
    for (kind, _), rows in zip(specs, groups):
        items.extend({"kind": kind, **row} for row in rows)
    return {"items": items[:50]}
