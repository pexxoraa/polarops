import asyncio

from core.time import utcnow
from integrations.open_meteo import fetch_current_weather
from integrations.polar_environment import (
    fetch_latest_sea_ice,
    fetch_polar_earthquakes,
    fetch_space_weather,
)
from repositories.external_cache import get_cached_json, put_cached_json
from repositories.locations import list_locations


def polar_region(expedition: dict, locations: list[dict]) -> str:
    region = str(expedition.get("region") or "").lower()
    if "antarctic" in region or "south" in region:
        return "south"
    if "arctic" in region or "north" in region:
        return "north"

    for location in locations:
        try:
            latitude = float(location.get("latitude"))
            if abs(latitude) >= 60:
                return "north" if latitude > 0 else "south"
        except Exception:
            pass
    return "south"


def primary_location(locations: list[dict]) -> dict | None:
    mapped = [
        item for item in locations
        if item.get("latitude") is not None and item.get("longitude") is not None
    ]
    if not mapped:
        return None
    for pattern in ("base", "station", "hub", "camp"):
        hit = next(
            (
                item for item in mapped
                if pattern in f"{item.get('name', '')} {item.get('type', '')}".lower()
            ),
            None,
        )
        if hit:
            return hit
    return mapped[0]


async def _cached(env, key: str, ttl: int, fetcher, force: bool = False):
    if not force:
        cached = await get_cached_json(env, key, ttl)
        if cached is not None:
            return cached
    payload = await fetcher()
    await put_cached_json(env, key, payload)
    return payload


async def _weather(env, base: dict, force: bool):
    lat = round(float(base["latitude"]), 4)
    lon = round(float(base["longitude"]), 4)
    async def load():
        payload = await fetch_current_weather(env, lat, lon)
        payload["location"] = base.get("name")
        payload["latitude"] = lat
        payload["longitude"] = lon
        return payload
    return await _cached(env, f"env:weather:{lat}:{lon}", 600, load, force)


async def _sea_ice(env, pole: str, force: bool):
    return await _cached(
        env,
        f"env:seaice:{pole}",
        21600,
        lambda: fetch_latest_sea_ice(pole),
        force,
    )


async def _space_weather(env, pole: str, force: bool):
    return await _cached(
        env,
        f"env:space:{pole}",
        300,
        lambda: fetch_space_weather(pole),
        force,
    )


async def _earthquakes(env, pole: str, force: bool):
    return await _cached(
        env,
        f"env:quakes:{pole}",
        600,
        lambda: fetch_polar_earthquakes(pole),
        force,
    )


RESOURCES = [
    {
        "name": "SCAR Antarctic Digital Database",
        "category": "topography",
        "regions": ["south"],
        "url": "https://add.scar.org/",
        "detail": (
            "BAS/SCAR coastline, ice-shelf fronts, contours, rock outcrop, "
            "lakes and related Antarctic mapping data."
        ),
        "update": "Current reference portal; release cadence is managed by BAS/SCAR.",
    },
    {
        "name": "BAS Ice Logistics Portal",
        "category": "ice operations",
        "regions": ["north", "south"],
        "url": "https://www.icelogistics.info/",
        "detail": (
            "Polar ice charts plus Sentinel-1/RADARSAT imagery catalogue "
            "for logistics and sea-ice awareness."
        ),
        "update": "Operational catalogue; product dates vary by provider.",
    },
    {
        "name": "NASA Worldview",
        "category": "satellite imagery",
        "regions": ["north", "south"],
        "url": "https://worldview.earthdata.nasa.gov/",
        "detail": "Near-real-time satellite imagery and scientific layers.",
        "update": "Many LANCE products are available within hours.",
    },
    {
        "name": "NOAA/NSIDC Sea Ice Index",
        "category": "sea ice",
        "regions": ["north", "south"],
        "url": "https://nsidc.org/data/seaice_index",
        "detail": "Daily Arctic and Antarctic sea-ice extent and concentration products.",
        "update": "Daily with an approximately one-day lag.",
    },
    {
        "name": "Antarctic Treaty EIES",
        "category": "operations",
        "regions": ["south"],
        "url": "https://www.ats.aq/s/information.html",
        "detail": "Official information exchange for Antarctic operational information.",
        "update": "Submitted by Antarctic Treaty Parties on the Treaty reporting cycle.",
    },
    {
        "name": "AntarcticGlaciers.org datasets",
        "category": "science reference",
        "regions": ["south"],
        "url": "https://www.antarcticglaciers.org/antarctica-2/antarctic-datasets/",
        "detail": "Curated discovery page for Antarctic glaciology datasets.",
        "update": "Reference/discovery resource, not an operational live feed.",
    },
    {
        "name": "INTERACT Virtual Access",
        "category": "Arctic observations",
        "regions": ["north"],
        "url": "https://dataportal.eu-interact.org/",
        "detail": "Research-station data portal for Arctic and northern datasets.",
        "update": "Dataset cadence varies by station and provider.",
    },
    {
        "name": "Sustaining Arctic Observing Networks (SAON)",
        "category": "Arctic observing systems",
        "regions": ["north"],
        "url": "https://arcticobserving.org/services",
        "detail": "Inventories and registries for Arctic observations and networks.",
        "update": "Registry/reference service; individual network cadence varies.",
    },
]


async def environment_overview(
    env,
    expedition: dict,
    force: bool = False,
) -> dict:
    locations = await list_locations(env, int(expedition["id"]))
    base = primary_location(locations)
    pole = polar_region(expedition, locations)

    tasks = [
        _sea_ice(env, pole, force),
        _space_weather(env, pole, force),
        _earthquakes(env, pole, force),
    ]
    labels = ["sea_ice", "space_weather", "earthquakes"]
    if base:
        tasks.insert(0, _weather(env, base, force))
        labels.insert(0, "weather")

    results = await asyncio.gather(*tasks, return_exceptions=True)
    data: dict = {}
    errors: dict = {}
    for label, value in zip(labels, results):
        if isinstance(value, Exception):
            errors[label] = str(value)
        else:
            data[label] = value

    data.setdefault("weather", None)
    return {
        "expedition": expedition,
        "pole": pole,
        "primary_location": base,
        "generated_at": utcnow(),
        "data": data,
        "errors": errors,
        "resources": [item for item in RESOURCES if pole in item["regions"]],
    }
