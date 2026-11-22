"""Exercise the actual setup command using isolated generated credentials."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_claims_postgres.py"


class ClaimsSetupTests(unittest.TestCase):
    def test_fresh_setup_creates_sql_and_separate_container_configuration(self):
        with TemporaryDirectory() as temporary:
            local = Path(temporary) / "accounts"
            runtime = Path(temporary) / "runtime"
            subprocess.run([sys.executable, str(SCRIPT), "--output-dir", str(local)], check=True, capture_output=True)
            self.assertEqual(local.stat().st_mode & 0o777, 0o700)
            self.assertEqual((local / "access.json").stat().st_mode & 0o777, 0o600)
            self.assertIn("CREATE TABLE IF NOT EXISTS", (local / "init-claims.sql").read_text())
            self.assertFalse((local / "init-claims.sh").exists())
            self.assertEqual(json.loads((local / "users.json").read_text()), json.loads((local / "users-container.json").read_text()))
            access = json.loads((local / "access.json").read_text())
            for account, password in access.items():
                credential = local / f"mcp-{account}.json"
                self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(credential.read_text()), {"username": account, "password": password})
            self.assertIn("@postgres:5432/", (local / "database-url-container").read_text())
            self.assertIn("@127.0.0.1:55438/", (local / "database-url").read_text())
            subprocess.run([sys.executable, str(SCRIPT), "--output-dir", str(local), "--docker-runtime", str(runtime)], check=True, capture_output=True)
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
            self.assertEqual({p.name for p in runtime.iterdir()}, {
                "admin-password", "reader-password", "init-claims.sql", "database-url-container", "users-container.json",
            })
            for path in runtime.iterdir():
                self.assertEqual(path.read_bytes(), (local / path.name).read_bytes())

    def test_repeated_setup_refuses_to_rotate_existing_credentials(self):
        with TemporaryDirectory() as temporary:
            local = Path(temporary) / "accounts"
            command = [sys.executable, str(SCRIPT), "--output-dir", str(local)]
            subprocess.run(command, check=True, capture_output=True)
            before = {p.name: p.read_bytes() for p in local.iterdir()}
            result = subprocess.run(command, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in local.iterdir()})
