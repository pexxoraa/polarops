from typing import Optional

from fastapi import APIRouter, Depends, Request

from api.dependencies import (
    current_user,
    ensure_expedition_access,
    require_permission,
)
from services.facility_service import (
    get_current_facility_weather,
    get_data_sources,
    get_public_directory,
    get_research_station_reference,
    import_facility,
    sync_comnap,
)

router = APIRouter()


@router.get("/api/data-sources")
async def data_sources(
    request: Request,
    user=Depends(current_user),
):
    return await get_data_sources(request.scope["env"])


@router.post("/api/public/facilities/sync")
async def sync_public_facilities(
    request: Request,
    offset: int = 0,
    limit: int = 35,
    user=Depends(require_permission("facilities.sync")),
):
    return await sync_comnap(request.scope["env"], offset, limit)


@router.get("/api/public/facilities")
async def list_public_facilities(
    request: Request,
    country: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    user=Depends(require_permission("facilities.read")),
):
    return await get_public_directory(
        request.scope["env"],
        country,
        q,
        limit,
        offset,
    )


@router.get("/api/public/research-stations-reference")
async def research_stations_reference(
    request: Request,
    user=Depends(require_permission("facilities.read")),
):
    return await get_research_station_reference(request.scope["env"])


@router.get("/api/public/facilities/{facility_id}/weather")
async def public_facility_weather(
    facility_id: int,
    request: Request,
    force: bool = False,
    user=Depends(require_permission("facilities.read")),
):
    return await get_current_facility_weather(
        request.scope["env"],
        facility_id,
        force,
    )


@router.post("/api/public/facilities/{facility_id}/import")
async def import_public_facility(
    facility_id: int,
    expedition_id: int,
    request: Request,
    user=Depends(require_permission("facilities.import")),
):
    env = request.scope["env"]
    await ensure_expedition_access(env, user, expedition_id)
    return await import_facility(env, facility_id, expedition_id, user)
