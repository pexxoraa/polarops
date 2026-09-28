import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from services.station_verification import (
    enrich_current_facility,
    verify_historical_reference,
)


class StationVerificationTests(unittest.TestCase):
    def test_geographic_scope_classification(self):
        treaty = enrich_current_facility({
            "name": "Treaty Base",
            "latitude": -75,
            "longitude": 20,
            "raw_json": "{}",
        })
        subantarctic = enrich_current_facility({
            "name": "Subantarctic",
            "latitude": -54.5,
            "longitude": 158.9,
            "raw_json": "{}",
        })
        anomaly = enrich_current_facility({
            "name": "Bad Antarctic Coordinate",
            "latitude": 69.6,
            "longitude": 76.4,
            "raw_json": "{}",
        })
        self.assertEqual(treaty["geographic_scope"], "antarctic_treaty_area")
        self.assertEqual(subantarctic["geographic_scope"], "subantarctic_reference")
        self.assertEqual(anomaly["geographic_scope"], "coordinate_anomaly")
        self.assertTrue(anomaly["coordinate_warning"])

    def test_historical_rows_are_not_promoted_without_current_evidence(self):
        historical = [
            {"map_number": 1, "station_name": "Vernadasky", "country": "Ukraine"},
            {"map_number": 30, "station_name": "Bird Is", "country": "United Kingdom"},
            {"map_number": 32, "station_name": "Sobral", "country": "Argentina"},
        ]
        current = [{
            "id": 10,
            "name": "Vernadsky",
            "country": "Ukraine",
            "facility_type": "Station",
            "seasonality": "Year-Round",
            "status": "Open",
            "latitude": -65.24,
            "longitude": -64.25,
            "raw_json": "{}",
        }]
        result = verify_historical_reference(historical, current)
        by_number = {row["map_number"]: row for row in result["items"]}
        self.assertEqual(by_number[1]["verification_status"], "current_updated_name")
        self.assertEqual(by_number[30]["verification_status"], "historical_subantarctic_not_current")
        self.assertEqual(by_number[32]["verification_status"], "historical_not_in_current_directory")
        self.assertEqual(result["summary"]["current_rows"], 1)
        self.assertEqual(result["summary"]["historical_only_rows"], 2)


if __name__ == "__main__":
    unittest.main()
