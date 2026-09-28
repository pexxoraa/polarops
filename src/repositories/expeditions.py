from database.d1 import q_first


async def get_expedition(env, expedition_id: int) -> dict | None:
    return await q_first(env, "SELECT * FROM expeditions WHERE id=?", expedition_id)
