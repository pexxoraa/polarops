from repositories.realtime_access import (
    get_realtime_expedition,
    get_realtime_user,
)


async def realtime_ticket_allows_expedition(
    env,
    expedition_id: int,
    payload: dict,
) -> bool:
    try:
        ticket_expedition_id = int(payload.get("eid", -1))
        ticket_user_id = int(payload.get("uid", -1))
        ticket_org_id = int(payload.get("oid", -1))
    except (TypeError, ValueError):
        return False

    if ticket_expedition_id != int(expedition_id):
        return False

    user = await get_realtime_user(env, ticket_user_id)
    expedition = await get_realtime_expedition(env, expedition_id)
    if not user or not expedition or not user.get("active"):
        return False

    return (
        int(user["organization_id"]) == ticket_org_id
        and int(expedition["organization_id"]) == ticket_org_id
    )
