import json
import re
import unicodedata


CURRENT_COMNAP_INFO_URL = "https://www.comnap.aq/antarctic-facilities-information"
CURRENT_COMNAP_PERIOD = "November 2024"

# Historical map row -> canonical name in the November 2024 COMNAP facilities list.
# The map has two Concordia rows (France and Italy) for one current joint facility.
HISTORICAL_TO_CURRENT = {
    1: "Vernadsky",
    2: "Palmer",
    3: "Brown Antartic Base",
    4: "Professor Julio Escudero",
    5: "Decepcion Antartic Base",
    6: "Cámara Antartic Base",
    7: "Risopatron",
    8: "Arturo Prat Antarctic Naval Base",
    9: "Juan Carlos I",
    10: "Ripamonti",
    11: "O'Higgins Base",
    12: "Great Wall",
    13: "Bellingshausen",
    14: "President Eduardo Frei Antarctic Base",
    15: "Artigas",
    16: "King Sejong",
    17: "Carlini Antartic Base",
    19: "Henryk Arctowski",
    20: "Comandante Ferraz",
    21: "St. Kliment Ohridski",
    22: "Machu Picchu",
    23: "Esperanza Antartic Base",
    24: "Gabriel Gonzalez Videla",
    25: "Marambio Antartic Base",
    26: "Primavera Antartic Base",
    27: "Signy",
    28: "Petrel Antartic Base",
    29: "Orcadas Antartic Base",
    33: "Belgrano II Antartic Base",
    34: "Halley VI",
    35: "Aboa",
    36: "SANAE IV",
    37: "Neumayer III",
    39: "Troll Station",
    40: "Tor",
    41: "Maitri",
    44: "Novolazarevskaya",
    45: "Wasa",
    46: "Asuka",
    47: "Dome Fuji",
    48: "Mizuho",
    49: "Syowa",
    50: "Molodezhnaya",
    53: "Mawson",
    54: "Soyuz",
    55: "Druzhnaya IV",
    56: "Progress",
    57: "Zhongshan",
    58: "Law",
    59: "Davis",
    60: "Amundsen-Scott South Pole",
    61: "Mirny",
    62: "Vostok",
    63: "Concordia",
    64: "Concordia",
    65: "Casey",
    67: "Dumont d'Urville",
    68: "Macquarie Island",
    69: "Mario Zucchelli",
    70: "McMurdo",
    71: "Scott Base",
    73: "San Martin Antartic Base",
    74: "Rothera",
    75: "Carvajal",
    76: "Matienzo Antartic Base",
    77: "Melchior Antartic Base",
    78: "Yelcho",
    79: "Pedro Vicente Maldonado",
    80: "Gabriel de Castilla Station",
    81: "Kohnen",
}

# Historical entries that are not in the current COMNAP open/temporarily-closed list.
# These are kept as historical references instead of being silently promoted to
# current operational facilities.
HISTORICAL_SUBANTARCTIC = {
    30,  # Bird Island
    31,  # Alfred-Faure (Crozet)
    42,  # Gough Island
    43,  # Marion Island
    51,  # Port-aux-Français / PAF (Kerguelen)
    52,  # Heard Island
    72,  # Martin-de-Viviès (Amsterdam Island)
    82,  # King Edward Point (South Georgia)
}
HISTORICAL_NOT_CURRENT = {
    18,  # Dallmann
    32,  # Sobral
    38,  # E-Base
    66,  # Law Dome
}
SUCCESSOR_ROWS = {34, 37}  # Halley -> Halley VI; Neumayer -> Neumayer III
JOINT_ROWS = {63, 64}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _raw_fields(facility: dict) -> dict:
    raw = facility.get("raw_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return {}


def enrich_current_facility(facility: dict) -> dict:
    result = dict(facility)
    raw = _raw_fields(result)
    result["year_established"] = raw.get("Year Established") or ""
    result["antarctic_region"] = raw.get("Antarctic Region") or ""
    result["elevation_m"] = raw.get("Elevation (meters)") or ""
    result["peak_population"] = raw.get("Peak Population") or ""
    result["power_supply_types"] = raw.get("Power Supply Types") or ""
    result["photo_url"] = raw.get("Photo URL") or ""
    result["webcam_url"] = raw.get("Webcam URL") or ""

    latitude = result.get("latitude")
    longitude = result.get("longitude")
    scope = "unknown"
    warning = ""
    try:
        lat = float(latitude)
        lon = float(longitude)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            scope = "coordinate_anomaly"
            warning = "Coordinates fall outside valid latitude/longitude limits."
        elif lat <= -60:
            scope = "antarctic_treaty_area"
        elif lat < 0:
            scope = "subantarctic_reference"
            warning = "Facility is north of 60°S and is outside the Antarctic Treaty area."
        else:
            scope = "coordinate_anomaly"
            warning = (
                "Source latitude is in the Northern Hemisphere although this record is "
                "listed in the Antarctic facilities dataset. PolarOps does not silently correct it."
            )
    except (TypeError, ValueError):
        scope = "coordinate_anomaly"
        warning = "Facility has no usable validated coordinates."

    result["geographic_scope"] = scope
    result["coordinate_warning"] = warning
    return result


def verify_historical_reference(historical_rows: list[dict], current_facilities: list[dict]) -> dict:
    current = [enrich_current_facility(row) for row in current_facilities]
    by_name = {str(row.get("name") or "").strip(): row for row in current}
    mapped_current_names = set(HISTORICAL_TO_CURRENT.values())

    verified_rows = []
    for source_row in historical_rows:
        row = dict(source_row)
        number = int(row["map_number"])
        current_name = HISTORICAL_TO_CURRENT.get(number)
        facility = by_name.get(current_name) if current_name else None

        if facility:
            if number in SUCCESSOR_ROWS:
                verification = "current_successor"
                note = "A current successor-generation station is listed by COMNAP."
            elif number in JOINT_ROWS:
                verification = "current_joint_facility"
                note = (
                    "The historical map lists France and Italy separately; the current "
                    "COMNAP directory represents Concordia as one jointly operated facility."
                )
            elif facility.get("geographic_scope") == "subantarctic_reference":
                verification = "current_subantarctic"
                note = (
                    "Still present in the current COMNAP facilities data, but the coordinates "
                    "are north of 60°S, outside the Antarctic Treaty area."
                )
            elif _normalize(row.get("station_name", "")) == _normalize(facility.get("name", "")):
                verification = "current_exact"
                note = "Historical name still corresponds directly to a current COMNAP facility."
            else:
                verification = "current_updated_name"
                note = "Current COMNAP uses an updated, expanded or corrected facility name."

            row.update({
                "verification_status": verification,
                "verification_note": note,
                "current_facility_id": facility.get("id"),
                "current_name": facility.get("name"),
                "current_status": facility.get("status"),
                "current_seasonality": facility.get("seasonality"),
                "current_type": facility.get("facility_type"),
                "current_scope": facility.get("geographic_scope"),
                "current_coordinate_warning": facility.get("coordinate_warning"),
            })
        elif number in HISTORICAL_SUBANTARCTIC:
            row.update({
                "verification_status": "historical_subantarctic_not_current",
                "verification_note": (
                    "Authentic historical-map entry, but it is not present in the November 2024 "
                    "COMNAP current facilities list and is a subantarctic reference."
                ),
                "current_facility_id": None,
                "current_name": None,
                "current_status": None,
            })
        else:
            row.update({
                "verification_status": "historical_not_in_current_directory",
                "verification_note": (
                    "Authentic historical-map entry, but it is not present in the November 2024 "
                    "COMNAP open/temporarily-closed facilities list. Do not treat it as a current base."
                ),
                "current_facility_id": None,
                "current_name": None,
                "current_status": None,
            })
        verified_rows.append(row)

    current_only = [
        row for row in current
        if str(row.get("name") or "").strip() not in mapped_current_names
    ]
    current_only.sort(
        key=lambda row: (
            0 if row.get("facility_type") == "Station" else 1,
            str(row.get("country") or ""),
            str(row.get("name") or ""),
        )
    )

    current_rows = sum(1 for row in verified_rows if str(row["verification_status"]).startswith("current_"))
    historical_only = len(verified_rows) - current_rows
    unique_current_matches = len({
        row.get("current_facility_id")
        for row in verified_rows
        if row.get("current_facility_id") is not None
    })
    temporarily_closed_matches = sum(
        1 for row in verified_rows if row.get("current_status") == "Temporarily Closed"
    )
    current_only_stations = sum(1 for row in current_only if row.get("facility_type") == "Station")

    scope_counts = {
        "antarctic_treaty_area": sum(1 for row in current if row["geographic_scope"] == "antarctic_treaty_area"),
        "subantarctic_reference": sum(1 for row in current if row["geographic_scope"] == "subantarctic_reference"),
        "coordinate_anomaly": sum(1 for row in current if row["geographic_scope"] == "coordinate_anomaly"),
    }

    return {
        "items": verified_rows,
        "current_only": current_only,
        "summary": {
            "historical_rows": len(verified_rows),
            "current_rows": current_rows,
            "unique_current_matches": unique_current_matches,
            "historical_only_rows": historical_only,
            "temporarily_closed_matches": temporarily_closed_matches,
            "current_facilities": len(current),
            "current_only_facilities": len(current_only),
            "current_only_stations": current_only_stations,
            "current_only_other_facilities": len(current_only) - current_only_stations,
            "scope_counts": scope_counts,
        },
    }
