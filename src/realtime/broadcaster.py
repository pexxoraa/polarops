import json

from core.time import utcnow


async def broadcast(
    env,
    expedition_id: int,
    event_type: str,
    entity_type: str,
    entity_id: int | None = None,
    data: dict | None = None,
):
    """Notify an expedition room after authoritative state is committed."""
    try:
        stub = env.EXPEDITION_ROOM.getByName(str(expedition_id))
        payload = json.dumps({
            "type": event_type,
            "expedition_id": expedition_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "server_time": utcnow(),
            "data": data or {},
        }, separators=(",", ":"))
        await stub.broadcast_json(payload)
    except Exception:
        # Realtime delivery is best-effort and must not undo persistent state.
        pass
