from datetime import datetime, timezone

from fastapi import HTTPException

from core.config import DEFAULT_COMNAP_URL, env_value
from integrations.comnap import fetch_facilities
from integrations.open_meteo import fetch_current_weather
from repositories.activity import log_activity
from repositories.data_sources import (
    external_worker_count,
    list_sources,
    public_facility_count,
    set_source_status,
)
from repositories.facilities import (
    get_facility_weather,
    get_public_facility,
    list_public_facilities,
    list_research_station_reference,
    research_station_reference_count,
    upsert_facilities,
    upsert_facility_weather,
)
from repositories.locations import upsert_external_location
from realtime.broadcaster import broadcast
from services.station_verification import (
    CURRENT_COMNAP_INFO_URL,
    CURRENT_COMNAP_PERIOD,
    enrich_current_facility,
    verify_historical_reference,
)


async def get_data_sources(env) -> dict:
    return {
        "sources": await list_sources(env),
        "facility_count": await public_facility_count(env),
        "research_station_reference_count": await research_station_reference_count(env),
        "external_worker_count": await external_worker_count(env),
        "worker_feed_configured": bool(env_value(env, "OPERATIONS_FEED_URL")),
    }


async def sync_comnap(env, offset: int = 0, limit: int = 35) -> dict:
    url = env_value(env, "COMNAP_FACILITIES_URL", DEFAULT_COMNAP_URL)
    try:
        url, all_facilities = await fetch_facilities(env)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 35))
        batch = all_facilities[offset:offset + limit]
        await upsert_facilities(env, batch)

        next_offset = offset + len(batch)
        has_more = next_offset < len(all_facilities)
        await set_source_status(
            env,
            "COMNAP Facilities",
            url,
            "OK",
            f"{next_offset}/{len(all_facilities)} validated rows processed",
        )
        return {
            "ok": True,
            "synced": len(batch),
            "skipped": 0,
            "processed": next_offset,
            "total_rows": len(all_facilities),
            "next_offset": next_offset,
            "has_more": has_more,
            "source": "COMNAP",
            "source_url": url,
        }
    except Exception as exc:
        await set_source_status(env, "COMNAP Facilities", url, "ERROR", str(exc)[:500])
        raise HTTPException(status_code=502, detail=f"COMNAP sync failed: {exc}")


async def get_public_directory(
    env,
    country: str | None = None,
    query: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    result = await list_public_facilities(env, country, query, limit, offset)
    result["items"] = [enrich_current_facility(row) for row in result.get("items", [])]
    result.update({
        "source": "COMNAP",
        "source_url": env_value(env, "COMNAP_FACILITIES_URL", DEFAULT_COMNAP_URL),
        "source_info_url": CURRENT_COMNAP_INFO_URL,
        "source_period": CURRENT_COMNAP_PERIOD,
    })
    return result


async def get_current_facility_weather(env, facility_id: int, force: bool = False) -> dict:
    facility = await get_public_facility(env, facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")
    if facility.get("latitude") is None or facility.get("longitude") is None:
        raise HTTPException(status_code=400, detail="Facility has no usable coordinates")

    cached = await get_facility_weather(env, facility_id)
    if cached and not force:
        try:
            fetched = datetime.fromisoformat(cached["fetched_at"])
            if (datetime.now(timezone.utc) - fetched).total_seconds() < 600:
                return {"facility": facility, "weather": cached, "cached": True}
        except Exception:
            pass

    try:
        weather = await fetch_current_weather(
            env,
            float(facility["latitude"]),
            float(facility["longitude"]),
        )
        await upsert_facility_weather(env, facility_id, weather)
        return {"facility": facility, "weather": weather, "cached": False}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Weather refresh failed: {exc}")


async def import_facility(
    env,
    facility_id: int,
    expedition_id: int,
    user: dict,
) -> dict:
    facility = await get_public_facility(env, facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")

    location_id, created = await upsert_external_location(
        env,
        expedition_id,
        facility["name"],
        facility.get("facility_type") or "Station",
        facility.get("latitude"),
        facility.get("longitude"),
        "COMNAP",
        str(facility_id),
    )
    verb = "imported" if created else "updated"
    await log_activity(
        env,
        expedition_id,
        "location",
        f"Public facility {facility['name']} ({facility.get('country') or 'Antarctica'}) "
        f"{verb} from COMNAP reference data",
        user["id"],
    )
    await broadcast(
        env,
        expedition_id,
        "location.public_imported",
        "location",
        location_id,
        {"facility_id": facility_id, "name": facility["name"]},
    )
    return {"ok": True, "location_id": location_id, "created": created}


async def get_research_station_reference(env) -> dict:
    rows = await list_research_station_reference(env)
    current = await list_public_facilities(env, None, None, 2000, 0)
    verification = verify_historical_reference(rows, current.get("items", []))
    return {
        **verification,
        "total": len(rows),
        "source": "COMNAP Research Stations Map",
        "source_period": "1998-2005",
        "source_url": "/research-stations-map.pdf",
        "classification": "historical_reference_verified",
        "current_source": "COMNAP Antarctic Facilities List",
        "current_source_period": CURRENT_COMNAP_PERIOD,
        "current_source_url": env_value(env, "COMNAP_FACILITIES_URL", DEFAULT_COMNAP_URL),
        "current_source_info_url": CURRENT_COMNAP_INFO_URL,
        "warning": (
            "Historical station list from the supplied COMNAP map, verified against the "
            "November 2024 COMNAP current facilities directory. Historical entries never "
            "overwrite current operational status."
        ),
    }
