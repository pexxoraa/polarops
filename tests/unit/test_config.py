import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from core.config import required_env_value


class Env:
    pass


class RequiredEnvironmentValueTests(unittest.TestCase):
    def test_returns_configured_secret(self):
        env = Env()
        env.AUTH_SECRET = "configured-secret"
        self.assertEqual(required_env_value(env, "AUTH_SECRET"), "configured-secret")

    def test_missing_secret_fails_closed(self):
        with self.assertRaises(RuntimeError):
            required_env_value(Env(), "AUTH_SECRET")


if __name__ == "__main__":
    unittest.main()
