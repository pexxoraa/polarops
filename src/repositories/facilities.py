from core.time import utcnow
from database.d1 import q_all, q_first, q_value, q_write, q_write_batch
from schemas.facility import NormalizedFacility


async def count_public_facilities(env) -> int:
    return int(await q_value(env, "SELECT COUNT(*) c FROM public_facilities") or 0)


async def list_public_facilities(
    env,
    country: str | None = None,
    query: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    where: list[str] = []
    params: list = []
    if country:
        where.append("lower(f.country)=lower(?)")
        params.append(country)
    if query:
        where.append(
            "(lower(f.name) LIKE lower(?) OR lower(f.country) LIKE lower(?) "
            "OR lower(f.programme) LIKE lower(?))"
        )
        like = f"%{query}%"
        params.extend([like, like, like])

    clause = " WHERE " + " AND ".join(where) if where else ""
    limit = max(1, min(int(limit), 2000))
    offset = max(0, int(offset))
    total = int(
        await q_value(
            env,
            f"SELECT COUNT(*) c FROM public_facilities f{clause}",
            *params,
        ) or 0
    )
    rows = await q_all(
        env,
        f"""SELECT f.*,w.temperature_c,w.wind_speed_kph,w.wind_gusts_kph,
        w.observed_at weather_observed_at,w.fetched_at weather_fetched_at
        FROM public_facilities f
        LEFT JOIN facility_weather w ON w.facility_id=f.id{clause}
        ORDER BY f.country,f.name LIMIT ? OFFSET ?""",
        *(params + [limit, offset]),
    )
    countries = await q_all(
        env,
        """SELECT country,COUNT(*) count FROM public_facilities
        WHERE country<>'' GROUP BY country ORDER BY country""",
    )
    return {"items": rows, "total": total, "countries": countries}


async def get_public_facility(env, facility_id: int) -> dict | None:
    return await q_first(env, "SELECT * FROM public_facilities WHERE id=?", facility_id)


async def get_facility_weather(env, facility_id: int) -> dict | None:
    return await q_first(env, "SELECT * FROM facility_weather WHERE facility_id=?", facility_id)


async def upsert_facility_weather(env, facility_id: int, weather: dict):
    await q_write(
        env,
        """INSERT INTO facility_weather(
          facility_id,temperature_c,apparent_temperature_c,relative_humidity,
          wind_speed_kph,wind_direction_deg,wind_gusts_kph,surface_pressure_hpa,
          snowfall_mm,weather_code,observed_at,source,fetched_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(facility_id) DO UPDATE SET
          temperature_c=excluded.temperature_c,
          apparent_temperature_c=excluded.apparent_temperature_c,
          relative_humidity=excluded.relative_humidity,
          wind_speed_kph=excluded.wind_speed_kph,
          wind_direction_deg=excluded.wind_direction_deg,
          wind_gusts_kph=excluded.wind_gusts_kph,
          surface_pressure_hpa=excluded.surface_pressure_hpa,
          snowfall_mm=excluded.snowfall_mm,
          weather_code=excluded.weather_code,
          observed_at=excluded.observed_at,
          source=excluded.source,
          fetched_at=excluded.fetched_at""",
        facility_id,
        weather.get("temperature_c"),
        weather.get("apparent_temperature_c"),
        weather.get("relative_humidity"),
        weather.get("wind_speed_kph"),
        weather.get("wind_direction_deg"),
        weather.get("wind_gusts_kph"),
        weather.get("surface_pressure_hpa"),
        weather.get("snowfall_mm"),
        weather.get("weather_code"),
        weather.get("observed_at"),
        weather.get("source", "Open-Meteo model current conditions"),
        weather.get("fetched_at", utcnow()),
    )


async def upsert_facilities(env, facilities: list[NormalizedFacility]):
    if not facilities:
        return
    synced_at = utcnow()
    queries: list[tuple[str, tuple]] = []
    for facility in facilities:
        queries.append((
            """INSERT INTO public_facilities(
              source_key,name,country,programme,facility_type,seasonality,status,
              latitude,longitude,source,source_url,source_updated_at,raw_json,synced_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_key) DO UPDATE SET
              name=excluded.name,country=excluded.country,programme=excluded.programme,
              facility_type=excluded.facility_type,seasonality=excluded.seasonality,
              status=excluded.status,latitude=excluded.latitude,longitude=excluded.longitude,
              source=excluded.source,source_url=excluded.source_url,
              source_updated_at=excluded.source_updated_at,raw_json=excluded.raw_json,
              synced_at=excluded.synced_at""",
            (
                facility.source_key,
                facility.name,
                facility.country,
                facility.programme,
                facility.facility_type,
                facility.seasonality,
                facility.status,
                facility.latitude,
                facility.longitude,
                facility.source,
                facility.source_url,
                facility.source_updated_at,
                facility.raw_json,
                synced_at,
            ),
        ))
    await q_write_batch(env, queries)


async def list_research_station_reference(env) -> list[dict]:
    return await q_all(
        env,
        """SELECT map_number,station_name,country,source_title,source_period,source_url
        FROM research_station_reference
        ORDER BY map_number""",
    )


async def research_station_reference_count(env) -> int:
    return int(
        await q_value(
            env,
            "SELECT COUNT(*) c FROM research_station_reference",
        ) or 0
    )
