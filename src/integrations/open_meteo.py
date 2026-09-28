import urllib.parse

from workers import fetch as cf_fetch

from core.config import DEFAULT_OPEN_METEO_URL, env_value
from core.time import utcnow
from database.d1 import to_py


async def fetch_current_weather(env, latitude: float, longitude: float) -> dict:
    latitude = float(latitude)
    longitude = float(longitude)
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Invalid weather coordinates")

    params = urllib.parse.urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "snowfall,weather_code,surface_pressure,wind_speed_10m,"
            "wind_direction_10m,wind_gusts_10m"
        ),
        "timezone": "UTC",
        "wind_speed_unit": "kmh",
        "temperature_unit": "celsius",
    })
    url = env_value(env, "OPEN_METEO_URL", DEFAULT_OPEN_METEO_URL) + "?" + params
    response = await cf_fetch(url)
    if int(response.status) >= 400:
        raise RuntimeError(f"Open-Meteo HTTP {response.status}")

    payload = to_py(await response.json()) or {}
    current = payload.get("current") or {}
    return {
        "temperature_c": current.get("temperature_2m"),
        "apparent_temperature_c": current.get("apparent_temperature"),
        "relative_humidity": current.get("relative_humidity_2m"),
        "wind_speed_kph": current.get("wind_speed_10m"),
        "wind_direction_deg": current.get("wind_direction_10m"),
        "wind_gusts_kph": current.get("wind_gusts_10m"),
        "surface_pressure_hpa": current.get("surface_pressure"),
        "snowfall_mm": current.get("snowfall"),
        "weather_code": current.get("weather_code"),
        "observed_at": current.get("time"),
        "source": "Open-Meteo model current conditions",
        "source_url": "https://open-meteo.com/",
        "fetched_at": utcnow(),
    }
