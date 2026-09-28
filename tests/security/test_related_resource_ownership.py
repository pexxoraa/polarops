import asyncio
import sqlite3
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from services.relationship_service import (
    valid_location_ids,
    valid_personnel_ids,
    valid_vehicle_ids,
)


class FakeStatement:
    def __init__(self, connection, sql):
        self.connection = connection
        self.sql = sql
        self.params = ()

    def bind(self, *params):
        self.params = params
        return self

    async def first(self):
        cursor = self.connection.execute(self.sql, self.params)
        row = cursor.fetchone()
        return dict(row) if row else None


class FakeD1:
    def __init__(self, connection):
        self.connection = connection

    def prepare(self, sql):
        return FakeStatement(self.connection, sql)


class FakeEnv:
    def __init__(self, connection):
        self.DB = FakeD1(connection)


class RelatedResourceOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE locations(id INTEGER PRIMARY KEY, expedition_id INTEGER NOT NULL);
            CREATE TABLE personnel(id INTEGER PRIMARY KEY, expedition_id INTEGER NOT NULL);
            CREATE TABLE vehicles(id INTEGER PRIMARY KEY, expedition_id INTEGER NOT NULL);
            INSERT INTO locations VALUES (10,1),(20,2);
            INSERT INTO personnel VALUES (11,1),(21,2);
            INSERT INTO vehicles VALUES (12,1),(22,2);
            """
        )
        self.env = FakeEnv(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_same_expedition_relationships_are_allowed(self):
        self.assertTrue(asyncio.run(valid_location_ids(self.env, 1, 10)))
        self.assertTrue(asyncio.run(valid_personnel_ids(self.env, 1, 11)))
        self.assertTrue(asyncio.run(valid_vehicle_ids(self.env, 1, 12)))

    def test_cross_expedition_relationships_are_rejected(self):
        self.assertFalse(asyncio.run(valid_location_ids(self.env, 1, 20)))
        self.assertFalse(asyncio.run(valid_personnel_ids(self.env, 1, 21)))
        self.assertFalse(asyncio.run(valid_vehicle_ids(self.env, 1, 22)))

    def test_mixed_location_set_is_rejected(self):
        self.assertFalse(asyncio.run(valid_location_ids(self.env, 1, 10, 20)))

    def test_empty_optional_relationships_are_allowed(self):
        self.assertTrue(asyncio.run(valid_location_ids(self.env, 1, None)))
        self.assertTrue(asyncio.run(valid_personnel_ids(self.env, 1, None)))
        self.assertTrue(asyncio.run(valid_vehicle_ids(self.env, 1, None)))


if __name__ == "__main__":
    unittest.main()
