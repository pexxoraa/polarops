from repositories.ownership import (
    locations_belong_to_expedition,
    personnel_belong_to_expedition,
    vehicles_belong_to_expedition,
)


async def valid_location_ids(env, expedition_id: int, *location_ids: int | None) -> bool:
    return await locations_belong_to_expedition(
        env,
        expedition_id,
        [value for value in location_ids if value is not None],
    )


async def valid_personnel_ids(env, expedition_id: int, *personnel_ids: int | None) -> bool:
    return await personnel_belong_to_expedition(
        env,
        expedition_id,
        [value for value in personnel_ids if value is not None],
    )


async def valid_vehicle_ids(env, expedition_id: int, *vehicle_ids: int | None) -> bool:
    return await vehicles_belong_to_expedition(
        env,
        expedition_id,
        [value for value in vehicle_ids if value is not None],
    )
