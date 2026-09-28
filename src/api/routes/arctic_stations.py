from fastapi import APIRouter, Depends, Request

from api.dependencies import require_permission
from services.arctic_station_service import get_arctic_research_network


router = APIRouter()


@router.get("/api/public/arctic-research-stations")
async def arctic_research_stations(
    request: Request,
    user=Depends(require_permission("facilities.read")),
):
    return await get_arctic_research_network(request.scope["env"])
