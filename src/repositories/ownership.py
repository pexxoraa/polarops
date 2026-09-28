from database.d1 import q_value


async def _ids_belong_to_expedition(
    env,
    table: str,
    expedition_id: int,
    resource_ids: list[int],
) -> bool:
    ids = sorted({int(value) for value in resource_ids if value is not None})
    if not ids:
        return True
    placeholders = ",".join("?" for _ in ids)
    count = int(
        await q_value(
            env,
            f"SELECT COUNT(*) c FROM {table} WHERE expedition_id=? AND id IN ({placeholders})",
            int(expedition_id),
            *ids,
        ) or 0
    )
    return count == len(ids)


async def locations_belong_to_expedition(env, expedition_id: int, location_ids: list[int]) -> bool:
    return await _ids_belong_to_expedition(env, "locations", expedition_id, location_ids)


async def personnel_belong_to_expedition(env, expedition_id: int, personnel_ids: list[int]) -> bool:
    return await _ids_belong_to_expedition(env, "personnel", expedition_id, personnel_ids)


async def vehicles_belong_to_expedition(env, expedition_id: int, vehicle_ids: list[int]) -> bool:
    return await _ids_belong_to_expedition(env, "vehicles", expedition_id, vehicle_ids)
