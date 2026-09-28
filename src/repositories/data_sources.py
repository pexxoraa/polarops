from core.time import utcnow
from database.d1 import q_all, q_value, q_write


async def set_source_status(
    env,
    name: str,
    url: str,
    status: str,
    details: str = "",
):
    await q_write(
        env,
        """INSERT INTO data_sources(name,source_url,last_sync,last_status,details)
        VALUES(?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET source_url=excluded.source_url,
        last_sync=excluded.last_sync,last_status=excluded.last_status,details=excluded.details""",
        name,
        url,
        utcnow(),
        status,
        details,
    )


async def list_sources(env) -> list[dict]:
    return await q_all(env, "SELECT * FROM data_sources ORDER BY name")


async def public_facility_count(env) -> int:
    return int(await q_value(env, "SELECT COUNT(*) c FROM public_facilities") or 0)


async def external_worker_count(env) -> int:
    return int(await q_value(env, "SELECT COUNT(*) c FROM personnel WHERE source LIKE 'feed:%'") or 0)
