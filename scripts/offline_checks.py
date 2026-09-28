from __future__ import annotations

import ast
import base64
import hashlib
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Python syntax across the modular backend.
python_files = sorted((ROOT / "src").rglob("*.py"))
for path in python_files:
    ast.parse(path.read_text(), filename=str(path))

# Apply every numbered migration to an isolated in-memory SQLite database.
con = sqlite3.connect(":memory:")
for migration in sorted((ROOT / "migrations").glob("*.sql")):
    con.executescript(migration.read_text())

assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 3
assert con.execute("SELECT COUNT(*) FROM expeditions").fetchone()[0] >= 2
assert con.execute("SELECT COUNT(*) FROM personnel").fetchone()[0] >= 6
assert con.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] >= 5
assert con.execute("SELECT COUNT(*) FROM research_station_reference").fetchone()[0] == 82

# Verify bundled demo password hashes after all migrations.
def verify(password: str, encoded: str) -> bool:
    algo, rounds, salt_b64, digest_b64 = encoded.split("$", 3)
    assert algo == "pbkdf2_sha256"
    salt = base64.urlsafe_b64decode(salt_b64.encode())
    expected = base64.urlsafe_b64decode(digest_b64.encode())
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
    return actual == expected

rows = dict(con.execute("SELECT email,password_hash FROM users").fetchall())
assert verify("PolarOps123!", rows["commander@polarops.local"])
assert verify("Logistics123!", rows["logistics@polarops.local"])
assert verify("Field123!", rows["field@polarops.local"])

# Basic route coverage: routes may now live in modular router files, not worker.py.
js = (ROOT / "public/static/app.js").read_text() + "\n" + (ROOT / "public/static/ops-features.js").read_text()
backend_source = "\n".join(path.read_text() for path in python_files)
required = [
    "/api/auth/login", "/api/me", "/api/expeditions", "/api/dashboard",
    "/api/locations", "/api/personnel", "/api/cargo", "/api/inventory",
    "/api/vehicles", "/api/assets", "/api/incidents", "/api/activity",
    "/api/data-sources", "/api/public/facilities", "/api/backup",
    "/api/telemetry/position", "/api/integrations/workers/status",
    "/api/bootstrap", "/api/environment/overview", "/api/public/arctic-research-stations",
    "/api/ops/summary", "/api/ops/routes", "/api/ops/alerts", "/api/ops/science",
    "/api/ops/comms", "/api/ops/readiness", "/api/ops/sitrep", "/api/ops/search", "/api/ops/audit",
]
for route in required:
    assert route in js, f"frontend missing {route}"
    assert route in backend_source, f"backend missing {route}"

print("POLAROPS_CLOUDFLARE_OFFLINE_CHECKS_PASS")
