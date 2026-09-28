import json
from datetime import datetime, timezone

from core.time import utcnow
from database.d1 import q_first, q_write


async def get_cached_json(env, cache_key: str, max_age_seconds: int) -> dict | None:
    row = await q_first(
        env,
        "SELECT payload_json,fetched_at FROM external_cache WHERE cache_key=?",
        cache_key,
    )
    if not row:
        return None

    try:
        fetched = datetime.fromisoformat(row["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        if age > max_age_seconds:
            return None
        payload = json.loads(row["payload_json"])
        if isinstance(payload, dict):
            payload["_cache_age_seconds"] = max(0, round(age))
        return payload
    except Exception:
        return None


async def put_cached_json(env, cache_key: str, payload: dict):
    await q_write(
        env,
        """INSERT INTO external_cache(cache_key,payload_json,fetched_at) VALUES(?,?,?)
        ON CONFLICT(cache_key) DO UPDATE SET
        payload_json=excluded.payload_json,fetched_at=excluded.fetched_at""",
        cache_key,
        json.dumps(payload, separators=(",", ":")),
        utcnow(),
    )
