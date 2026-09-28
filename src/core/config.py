APP_VERSION = "2.1.0-cloudflare"
DEFAULT_COMNAP_URL = "https://www.comnap.aq/s/Facilities_Nov2024.csv"
DEFAULT_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_NSIDC_BASE = "https://noaadata.apps.nsidc.org/NOAA/G02135"
DEFAULT_SWPC_KP_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
DEFAULT_SWPC_OVATION_BASE = "https://services.swpc.noaa.gov/products/animations"
DEFAULT_USGS_EVENT_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
TOKEN_HOURS = 12


def env_value(env, name: str, default: str = "") -> str:
    try:
        value = getattr(env, name)
        if value is None:
            return default
        return str(value)
    except Exception:
        return default


def required_env_value(env, name: str) -> str:
    value = env_value(env, name).strip()
    if not value:
        raise RuntimeError(f"Required environment binding {name} is not configured")
    return value
