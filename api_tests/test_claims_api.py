"""Exercise the local HTTP contract with real demo authentication."""

import json
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from auth import hash_password
from claims_api import app


class ClaimsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = cls.enterClassContext(TemporaryDirectory())
        users = Path(directory) / "users.json"
        password_hash = hash_password("test-only-password")
        users.write_text(json.dumps(dict.fromkeys(("gandalf", "galadriel"), password_hash)))
        cls.enterClassContext(patch.dict(os.environ, {"RAG_USERS_FILE": str(users)}))
        cls.client = cls.enterClassContext(TestClient(app))

    def ask(self, question, account):
        return self.client.post(
            "/ask", json={"question": question}, auth=(account, "test-only-password")
        )

    def test_owner_sees_claim_and_policy(self):
        response = self.ask("Wie ist der Stand meiner Abrechnung RK-1042?", "gandalf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        result = response.json()
        self.assertEqual(result["status"], "answered")
        self.assertIn("Teilweise erstattet", result["answer"])
        self.assertEqual(result["sources"][0]["title"], "Private Ausgaben bei Dienstreisen")

    def test_other_account_cannot_distinguish_claim_from_missing_record(self):
        foreign = self.ask("Wie ist der Stand von RK-1042?", "galadriel")
        missing = self.ask("Wie ist der Stand von RK-9999?", "galadriel")
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.json(), missing.json())
        self.assertEqual(foreign.json()["status"], "not_found")
        self.assertEqual(foreign.json()["sources"], [])

    def test_shared_policy_is_available_to_both_accounts(self):
        gandalf = self.ask("Wird die Minibar erstattet?", "gandalf").json()
        galadriel = self.ask("Wird die Minibar erstattet?", "galadriel").json()
        self.assertEqual(gandalf, galadriel)
        self.assertEqual(gandalf["status"], "answered")
        self.assertIn("nicht erstattet", gandalf["answer"])

    def test_missing_or_multiple_ids_request_clarification(self):
        for question in ("Wie ist der Status?", "Vergleiche RK-1042 und RK-9999."):
            with self.subTest(question=question):
                result = self.ask(question, "gandalf").json()
                self.assertEqual(result["status"], "clarify")
                self.assertEqual(result["sources"], [])

    def test_authentication_and_request_validation(self):
        response = self.client.post("/ask", json={"question": "RK-1042"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.ask("RK-1042", "unknown").status_code, 401)
        self.assertEqual(self.ask("   ", "gandalf").status_code, 422)

    def test_database_outage_returns_safe_uncached_503_on_both_routes(self):
        for method, path, payload in (
            ("GET", "/claims/RK-1042", None),
            ("POST", "/ask", {"question": "Wie ist der Stand von RK-1042?"}),
        ):
            with self.subTest(route=path):
                failure = sqlite3.OperationalError("fake-secret /private/claims.sqlite SELECT owner")
                with patch("claims.sqlite3.connect", side_effect=failure):
                    response = self.client.request(
                        method, path, json=payload, auth=("gandalf", "test-only-password")
                    )
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": "Claim service unavailable"})
                self.assertEqual(response.headers["cache-control"], "no-store")
                for sensitive in ("fake-secret", "/private/claims.sqlite", "SELECT", "gandalf"):
                    self.assertNotIn(sensitive, response.text)

    def test_outage_does_not_bypass_authentication(self):
        with patch("claims.sqlite3.connect", side_effect=sqlite3.OperationalError("test outage")) as connect:
            self.assertEqual(self.client.get("/claims/RK-1042").status_code, 401)
            self.assertEqual(self.client.post("/ask", json={"question": "RK-1042"}).status_code, 401)
            self.assertEqual(self.ask("RK-1042", "unknown").status_code, 401)
            connect.assert_not_called()

    def test_policy_question_works_without_claim_database(self):
        with patch("claims.sqlite3.connect", side_effect=sqlite3.OperationalError("test outage")) as connect:
            response = self.ask("Wird die Minibar erstattet?", "gandalf")
            connect.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "answered")

    def test_api_recovers_on_next_request(self):
        with patch("claims.sqlite3.connect", side_effect=sqlite3.OperationalError("test outage")):
            self.assertEqual(self.ask("RK-1042", "gandalf").status_code, 503)
        response = self.ask("RK-1042", "gandalf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "answered")


