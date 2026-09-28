from fastapi import APIRouter, Depends, Request

from api.dependencies import ensure_expedition_access, require_permission
from services.environment_service import environment_overview

router = APIRouter()


@router.get("/api/environment/overview")
async def get_environment_overview(
    expedition_id: int,
    request: Request,
    force: bool = False,
    user=Depends(require_permission("environment.read")),
):
    env = request.scope["env"]
    expedition = await ensure_expedition_access(env, user, expedition_id)
    return await environment_overview(env, expedition, force)
