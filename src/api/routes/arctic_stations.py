from fastapi import APIRouter, Depends

from api.dependencies import current_user
from core.permissions import require_permission
from services.arctic_station_service import get_arctic_research_network


router = APIRouter()


@router.get("/api/public/arctic-research-stations")
async def arctic_research_stations(user=Depends(current_user)):
    env = user["env"]
    require_permission(user, "facilities.read")
    return await get_arctic_research_network(env)
