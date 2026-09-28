import csv
import hashlib
import io
import json

from workers import fetch as cf_fetch

from core.config import DEFAULT_COMNAP_URL, env_value
from integrations.parsing import parse_coord, pick
from schemas.facility import NormalizedFacility


async def fetch_facilities(env) -> tuple[str, list[NormalizedFacility]]:
    """Fetch, normalize and validate the official COMNAP facilities CSV."""
    url = env_value(env, "COMNAP_FACILITIES_URL", DEFAULT_COMNAP_URL)
    response = await cf_fetch(url)
    if int(response.status) >= 400:
        raise RuntimeError(f"COMNAP HTTP {response.status}")

    # COMNAP currently serves the CSV using a Windows-1252/Latin-1 compatible
    # encoding. Response.text() assumes UTF-8 and replaces accented characters
    # (for example Cámara and Bahía) with U+FFFD. Decode the raw response using
    # the Encoding Standard so station names remain authoritative.
    body = await response.bytes()
    text = body.decode("windows-1252")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    facilities: list[NormalizedFacility] = []

    for raw in reader:
        name = pick(
            raw,
            "English Name",
            "Official Name",
            "Facility Name",
            "Facility",
            "Station Name",
            "Name",
        )
        if not name:
            continue

        country = pick(
            raw,
            "Operator (primary)",
            "Country",
            "Party",
            "Nation",
            "National Antarctic Program Country",
            "NAP Country",
        )
        primary_operator = pick(raw, "Operator (primary)")
        additional_operator = pick(raw, "Operator (additional)")
        programme = " / ".join(
            value for value in (primary_operator, additional_operator) if value
        ) or pick(
            raw,
            "National Antarctic Program",
            "National Antarctic Programme",
            "Operator",
            "Programme",
            "Program",
        )

        latitude = parse_coord(
            pick(raw, "Latitude (DD)", "Latitude", "Latitude DD", "Lat", "Y")
        )
        longitude = parse_coord(
            pick(raw, "Longitude (DD)", "Longitude", "Longitude DD", "Lon", "Lng", "Long", "X")
        )
        if latitude is not None and not -90 <= latitude <= 90:
            latitude = None

        external_id = pick(raw, "Record ID#", "Facility ID", "ID", "Identifier", "Code")
        if external_id:
            # COMNAP Record ID is stable even when a facility is renamed or its
            # coordinates/status change. Keep the source key stable across syncs.
            source_key = f"comnap:{external_id}"
        else:
            basis = f"{name}|{country}|{latitude}|{longitude}"
            source_key = "comnap:" + hashlib.sha256(basis.encode()).hexdigest()[:24]

        facilities.append(
            NormalizedFacility(
                source_key=source_key,
                name=name,
                country=country,
                programme=programme,
                facility_type=pick(raw, "Facility Type", "Type") or "Facility",
                seasonality=pick(
                    raw,
                    "Operational Period",
                    "Seasonality",
                    "Operation",
                    "Operational Status",
                ),
                status=pick(raw, "Status", "Facility Status"),
                latitude=latitude,
                longitude=longitude,
                source_url=url,
                raw_json=json.dumps(raw, ensure_ascii=False),
            )
        )

    return url, facilities
