"""Restore a fictional PostgreSQL snapshot into a fresh database and check access."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from claims_api import app
from evaluation.claim_service import ContractResult

ROOT = Path(__file__).resolve().parents[1]


def database_command(*arguments, input_file=None, output_file=None):
    return subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "compose.claims.yaml"),
         "exec", "-T", "postgres", *arguments],
        cwd=ROOT, stdin=input_file, stdout=output_file or subprocess.PIPE,
        stderr=subprocess.PIPE, check=True, timeout=60,
    )


class BackupRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        local = ROOT / ".claims-local"
        cls.access = json.loads((local / "access.json").read_text())
        original_url = (local / "database-url").read_text().strip()
        cls.database_name = "restore_" + uuid4().hex
        cls.url = make_conninfo(original_url, dbname=cls.database_name)
        temporary = tempfile.TemporaryDirectory(prefix="claims-restore-")
        cls.addClassCleanup(temporary.cleanup)
        archive = Path(temporary.name) / "snapshot.dump"
        start = time.monotonic()
        # Exclusive creation gives the archive owner-only permissions.
        with archive.open("xb") as output:
            archive.chmod(0o600)
            database_command("pg_dump", "-U", "claims_admin", "-d", "claims_demo",
                             "--format=custom", output_file=output)
        database_command("createdb", "-U", "claims_admin", cls.database_name)
        cls.addClassCleanup(
            database_command, "dropdb", "-U", "claims_admin", "--if-exists", cls.database_name,
        )
        with archive.open("rb") as source:
            database_command("pg_restore", "-U", "claims_admin", "-d", cls.database_name,
                             "--exit-on-error", input_file=source)
        cls.restore_seconds = round(time.monotonic() - start, 3)
        cls.enterClassContext(patch.dict(os.environ, {
            "CLAIMS_BACKEND": "postgres",
            "CLAIMS_DATABASE_URL_FILE": "",
            "CLAIMS_DATABASE_URL": cls.url,
            "RAG_USERS_FILE": str(local / "users.json"),
        }))
        cls.client = cls.enterClassContext(TestClient(app))

    def test_restored_records_are_complete(self):
        with psycopg.connect(self.url, connect_timeout=3) as database:
            self.assertEqual(database.execute(
                "SELECT id, owner, status, deduction_reason FROM public.claims ORDER BY id"
            ).fetchall(), [
                ("RK-1042", "gandalf", "Teilweise erstattet", "Minibar ist eine private Ausgabe."),
            ])

    def test_owner_can_read_restored_claim_through_application(self):
        response = self.client.get(
            "/claims/RK-1042", auth=("gandalf", self.access["gandalf"]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Teilweise erstattet")

    def test_foreign_account_still_cannot_read_restored_claim(self):
        foreign = self.client.get(
            "/claims/RK-1042", auth=("galadriel", self.access["galadriel"]),
        )
        missing = self.client.get(
            "/claims/RK-9999", auth=("galadriel", self.access["galadriel"]),
        )
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.json(), missing.json())

    def test_restored_permissions_still_prevent_writes(self):
        for statement in (
            "UPDATE public.claims SET status = 'changed'",
            "DELETE FROM public.claims",
            "INSERT INTO public.claims VALUES ('RK-9999', 'gandalf', 'fake', 'fake')",
            "CREATE TABLE public.forbidden_restore_write (id integer)",
        ):
            with self.subTest(statement=statement):
                with psycopg.connect(self.url, connect_timeout=3) as database:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        database.execute(statement)
                    database.rollback()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = ContractResult()
    unittest.TestLoader().loadTestsFromTestCase(BackupRestoreTests).run(result)
    report = {
        "scope": "Fictional PostgreSQL backup restored into a fresh database",
        "tests_run": result.testsRun,
        "passed": sum(row["status"] == "passed" for row in result.rows),
        "successful": result.wasSuccessful() and result.testsRun == 4,
        "snapshot_and_restore_seconds": getattr(BackupRestoreTests, "restore_seconds", None),
        "results": result.rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["successful"]:
        print("Restore checks failed. Check the local stack and test assertions.")
    return int(not report["successful"])


if __name__ == "__main__":
    raise SystemExit(main())
