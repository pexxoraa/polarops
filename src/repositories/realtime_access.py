from database.d1 import q_first


async def get_realtime_user(env, user_id: int) -> dict | None:
    return await q_first(
        env,
        "SELECT id,organization_id,active FROM users WHERE id=?",
        int(user_id),
    )


async def get_realtime_expedition(env, expedition_id: int) -> dict | None:
    return await q_first(
        env,
        "SELECT id,organization_id FROM expeditions WHERE id=?",
        int(expedition_id),
    )
