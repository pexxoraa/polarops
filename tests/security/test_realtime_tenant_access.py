import asyncio
import sqlite3
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from services.realtime_access_service import realtime_ticket_allows_expedition


class FakeStatement:
    def __init__(self, connection, sql):
        self.connection = connection
        self.sql = sql
        self.params = ()

    def bind(self, *params):
        self.params = params
        return self

    async def first(self):
        row = self.connection.execute(self.sql, self.params).fetchone()
        return dict(row) if row else None


class FakeD1:
    def __init__(self, connection):
        self.connection = connection

    def prepare(self, sql):
        return FakeStatement(self.connection, sql)


class FakeEnv:
    def __init__(self, connection):
        self.DB = FakeD1(connection)


class RealtimeTenantAccessTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE users(
              id INTEGER PRIMARY KEY,
              organization_id INTEGER NOT NULL,
              active INTEGER NOT NULL
            );
            CREATE TABLE expeditions(
              id INTEGER PRIMARY KEY,
              organization_id INTEGER NOT NULL
            );
            INSERT INTO users VALUES (1,10,1),(2,20,1),(3,10,0);
            INSERT INTO expeditions VALUES (100,10),(200,20);
            """
        )
        self.env = FakeEnv(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_same_tenant_ticket_is_allowed(self):
        payload = {"uid": 1, "oid": 10, "eid": 100}
        self.assertTrue(asyncio.run(
            realtime_ticket_allows_expedition(self.env, 100, payload)
        ))

    def test_cross_tenant_expedition_is_denied(self):
        payload = {"uid": 1, "oid": 10, "eid": 200}
        self.assertFalse(asyncio.run(
            realtime_ticket_allows_expedition(self.env, 200, payload)
        ))

    def test_ticket_cannot_be_reused_for_different_expedition(self):
        payload = {"uid": 1, "oid": 10, "eid": 100}
        self.assertFalse(asyncio.run(
            realtime_ticket_allows_expedition(self.env, 200, payload)
        ))

    def test_inactive_user_is_denied(self):
        payload = {"uid": 3, "oid": 10, "eid": 100}
        self.assertFalse(asyncio.run(
            realtime_ticket_allows_expedition(self.env, 100, payload)
        ))


if __name__ == "__main__":
    unittest.main()
