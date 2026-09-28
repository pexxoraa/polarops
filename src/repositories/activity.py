from core.time import utcnow
from database.d1 import q_write


async def log_activity(
    env,
    expedition_id: int,
    category: str,
    message: str,
    user_id: int | None = None,
):
    await q_write(
        env,
        "INSERT INTO activity(expedition_id,category,message,user_id,created_at) VALUES(?,?,?,?,?)",
        expedition_id,
        category,
        message,
        user_id,
        utcnow(),
    )
