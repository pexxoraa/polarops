from database.d1 import last_row_id, q_all, q_first, q_write


async def list_locations(env, expedition_id: int) -> list[dict]:
    return await q_all(
        env,
        "SELECT * FROM locations WHERE expedition_id=? ORDER BY id",
        expedition_id,
    )


async def find_location_by_name(env, expedition_id: int, name: str) -> dict | None:
    return await q_first(
        env,
        "SELECT * FROM locations WHERE expedition_id=? AND name=?",
        expedition_id,
        name,
    )


async def upsert_external_location(
    env,
    expedition_id: int,
    name: str,
    location_type: str,
    latitude: float | None,
    longitude: float | None,
    source: str,
    external_id: str | None = None,
) -> tuple[int, bool]:
    existing = await find_location_by_name(env, expedition_id, name)
    if existing:
        await q_write(
            env,
            """UPDATE locations
            SET type=?,latitude=?,longitude=?,source=?,external_id=COALESCE(?,external_id)
            WHERE id=?""",
            location_type,
            latitude,
            longitude,
            source,
            external_id,
            existing["id"],
        )
        return int(existing["id"]), False

    result = await q_write(
        env,
        """INSERT INTO locations(expedition_id,name,type,latitude,longitude,source,external_id)
        VALUES(?,?,?,?,?,?,?)""",
        expedition_id,
        name,
        location_type,
        latitude,
        longitude,
        source,
        external_id,
    )
    return last_row_id(result), True
