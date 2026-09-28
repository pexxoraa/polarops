from fastapi import APIRouter, Request

from core.config import APP_VERSION, env_value
from core.time import utcnow
from repositories.facilities import (
    count_public_facilities,
    research_station_reference_count,
)

router = APIRouter()


@router.get("/api/health")
async def health(request: Request):
    env = request.scope["env"]
    return {
        "ok": True,
        "time": utcnow(),
        "database": "Cloudflare D1",
        "realtime": True,
        "durable_objects": True,
        "public_facilities": await count_public_facilities(env),
        "research_station_reference": await research_station_reference_count(env),
        "worker_feed_configured": bool(env_value(env, "OPERATIONS_FEED_URL")),
        "version": APP_VERSION,
    }
