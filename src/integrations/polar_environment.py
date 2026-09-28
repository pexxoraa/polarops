import asyncio
import re
import time
import urllib.parse
from datetime import datetime, timezone

from workers import fetch as cf_fetch

from core.config import (
    DEFAULT_NSIDC_BASE,
    DEFAULT_SWPC_KP_URL,
    DEFAULT_SWPC_OVATION_BASE,
    DEFAULT_USGS_EVENT_URL,
)
from core.time import utcnow
from database.d1 import to_py


async def fetch_latest_sea_ice(pole: str) -> dict:
    now = datetime.now(timezone.utc)
    months = [(now.year, now.month)]
    months.append((now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1))

    hemisphere = "north" if pole == "north" else "south"
    prefix = "N" if pole == "north" else "S"
    month_names = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]

    found = None
    for year, month in months:
        folder = f"{month:02d}_{month_names[month - 1]}"
        base = f"{DEFAULT_NSIDC_BASE}/{hemisphere}/daily/images/{year}/{folder}/"
        response = await cf_fetch(base)
        if int(response.status) >= 400:
            continue
        html = await response.text()
        matches = re.findall(
            rf'href="({prefix}_(\d{{8}})_conc_blmrbl_v4\.0\.png)"',
            html,
        )
        if matches:
            filename, datestr = sorted(matches, key=lambda item: item[1])[-1]
            found = (base, filename, datestr)
            break

    if not found:
        raise RuntimeError("NSIDC latest daily sea-ice image not found")

    base, concentration_name, datestr = found
    return {
        "hemisphere": pole,
        "date": f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:8]}",
        "concentration_image": base + concentration_name,
        "extent_image": base + concentration_name.replace("_conc_", "_extn_"),
        "source": "NOAA/NSIDC Sea Ice Index, Version 4",
        "source_url": "https://nsidc.org/data/seaice_index",
        "freshness": "Daily, normally about one day behind",
        "fetched_at": utcnow(),
    }


async def fetch_space_weather(pole: str) -> dict:
    kp_response, aurora_response = await asyncio.gather(
        cf_fetch(DEFAULT_SWPC_KP_URL),
        cf_fetch(f"{DEFAULT_SWPC_OVATION_BASE}/ovation_{pole}_24h.json"),
    )
    if int(kp_response.status) >= 400:
        raise RuntimeError(f"SWPC Kp HTTP {kp_response.status}")

    kp_rows = to_py(await kp_response.json()) or []
    latest_kp = kp_rows[-1] if kp_rows else {}

    aurora = {}
    if int(aurora_response.status) < 400:
        frames = to_py(await aurora_response.json()) or []
        latest_frame = frames[-1] if frames else {}
        if latest_frame:
            aurora = {
                "time_tag": latest_frame.get("time_tag"),
                "image_url": "https://services.swpc.noaa.gov" + str(latest_frame.get("url") or ""),
            }

    estimated_kp = float(latest_kp.get("estimated_kp") or latest_kp.get("kp_index") or 0)
    level = (
        "high" if estimated_kp >= 7 else
        "elevated" if estimated_kp >= 5 else
        "moderate" if estimated_kp >= 4 else
        "low"
    )
    return {
        "kp_index": latest_kp.get("kp_index"),
        "estimated_kp": estimated_kp,
        "time_tag": latest_kp.get("time_tag"),
        "communications_risk": level,
        "aurora": aurora,
        "source": "NOAA Space Weather Prediction Center",
        "source_url": "https://www.swpc.noaa.gov/",
        "fetched_at": utcnow(),
    }


async def fetch_polar_earthquakes(pole: str) -> dict:
    start = datetime.fromtimestamp(time.time() - 30 * 86400, timezone.utc).strftime("%Y-%m-%d")
    params = {
        "format": "geojson",
        "starttime": start,
        "minmagnitude": 4.0,
        "orderby": "time",
        "limit": 20,
    }
    if pole == "north":
        params["minlatitude"] = 60
    else:
        params["maxlatitude"] = -60

    response = await cf_fetch(
        DEFAULT_USGS_EVENT_URL + "?" + urllib.parse.urlencode(params)
    )
    if int(response.status) >= 400:
        raise RuntimeError(f"USGS HTTP {response.status}")

    raw = to_py(await response.json()) or {}
    events = []
    for feature in (raw.get("features") or [])[:20]:
        properties = feature.get("properties") or {}
        coordinates = (feature.get("geometry") or {}).get("coordinates") or [None, None, None]
        milliseconds = properties.get("time")
        occurred_at = (
            datetime.fromtimestamp(float(milliseconds) / 1000, timezone.utc).isoformat()
            if milliseconds else None
        )
        events.append({
            "id": feature.get("id"),
            "magnitude": properties.get("mag"),
            "place": properties.get("place"),
            "time": occurred_at,
            "longitude": coordinates[0],
            "latitude": coordinates[1],
            "depth_km": coordinates[2],
            "detail_url": properties.get("url"),
        })

    return {
        "period_days": 30,
        "minimum_magnitude": 4.0,
        "count": len(events),
        "events": events,
        "source": "USGS Earthquake Hazards Program",
        "source_url": "https://earthquake.usgs.gov/",
        "fetched_at": utcnow(),
    }
