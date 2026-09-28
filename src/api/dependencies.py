from fastapi import Depends, HTTPException, Request

from core.config import required_env_value
from core.permissions import has_permission
from core.security import decode_token
from database.d1 import q_first
from repositories.expeditions import get_expedition


async def current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    env = request.scope["env"]
    secret = required_env_value(env, "AUTH_SECRET")
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
    """Compatibility role dependency while domains migrate to explicit permissions."""
    async def dep(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permission")
        return user

    return dep


def require_permission(permission: str):
    async def dep(user=Depends(current_user)):
        if not has_permission(user, permission):
            raise HTTPException(status_code=403, detail="Insufficient permission")
        return user

    return dep


async def ensure_expedition_access(env, user: dict, expedition_id: int) -> dict:
    expedition = await get_expedition(env, expedition_id)
    if not expedition or int(expedition["organization_id"]) != int(user["organization_id"]):
        raise HTTPException(status_code=404, detail="Expedition not found")
    return expedition
