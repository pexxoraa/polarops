import asyncio
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import services.arctic_station_service as service


class ArcticStationServiceTests(unittest.TestCase):
    def test_summary_keeps_reference_and_verified_current_separate(self):
        original = service.list_arctic_research_stations

        async def fake(_env):
            return [
                {"source_kind": "user_reference", "verification_status": "verified_current", "latitude": 70.0, "longitude": 20.0, "geographic_scope": "arctic_circle"},
                {"source_kind": "user_reference", "verification_status": "reference_unverified", "latitude": 64.0, "longitude": -110.0, "geographic_scope": "subarctic"},
                {"source_kind": "current_network_addition", "verification_status": "verified_current", "latitude": None, "longitude": None, "geographic_scope": "unmapped"},
            ]

        service.list_arctic_research_stations = fake
        try:
            result = asyncio.run(service.get_arctic_research_network(object()))
        finally:
            service.list_arctic_research_stations = original

        self.assertEqual(result["summary"]["user_reference_rows"], 2)
        self.assertEqual(result["summary"]["verified_reference_rows"], 1)
        self.assertEqual(result["summary"]["reference_unverified_rows"], 1)
        self.assertEqual(result["summary"]["current_network_additions"], 1)
        self.assertEqual(result["summary"]["mapped_rows"], 2)


if __name__ == "__main__":
    unittest.main()
