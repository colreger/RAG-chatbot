"""Real API privacy boundaries for local telemetry, including error paths."""

import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from auth import hash_password
from claims import ClaimServiceUnavailable
from claims_api import app
from request_observability import RequestTelemetry


class RequestObservabilityTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        users = root / "users.json"
        users.write_text(json.dumps({"gandalf": hash_password("fake-private-password")}))
        self.enterContext(patch.dict(os.environ, {"RAG_USERS_FILE": str(users), "CLAIMS_BACKEND": "sqlite-demo"}))
        self.client = self.enterContext(TestClient(app))
        self.auth = ("gandalf", "fake-private-password")

    def test_ids_are_fresh_server_generated_and_errors_are_uncacheable(self):
        received = []
        for method, path, kwargs in (
            ("GET", "/healthz", {}),
            ("GET", "/claims/RK-1042", {}),
            ("GET", "/claims/RK-1042", {"auth": self.auth}),
            ("POST", "/ask", {"auth": self.auth, "json": {"question": " "}}),
            ("GET", "/fake-private-path", {}),
        ):
            response = self.client.request(method, path, headers={"X-Request-ID": "a" * 32}, **kwargs)
            request_id = response.headers["x-request-id"]
            self.assertRegex(request_id, r"^[a-f0-9]{32}$")
            self.assertNotEqual(request_id, "a" * 32)
            self.assertEqual(response.headers["cache-control"], "no-store")
            received.append(request_id)
        self.assertEqual(len(set(received)), len(received))

    def test_logs_omit_content_headers_raw_paths_and_exception_details(self):
        with self.assertLogs("support.requests", level="INFO") as captured:
            self.client.post("/ask?private-query=fake-sensitive-query", auth=self.auth,
                             headers={"X-Request-ID": "fake-secret-header"},
                             json={"question": "fake-private-question RK-1042"})
            self.client.get("/fake-sensitive-path", auth=self.auth)
            with patch("claims.lookup_demo_claim", side_effect=ClaimServiceUnavailable()):
                response = self.client.get("/claims/RK-1042", auth=self.auth)
                self.assertEqual(response.status_code, 503)
        serialized = "\n".join(captured.output)
        for secret in ("fake-private-password", "fake-sensitive-query", "fake-secret-header", "fake-private-question", "fake-sensitive-path", "RK-1042", "gandalf", "Authorization"):
            self.assertNotIn(secret, serialized)
        events = [json.loads(record.getMessage()) for record in captured.records]
        for event in events:
            self.assertEqual(set(event), {"event", "timestamp", "request_id", "route", "method", "status_code", "duration_ms"})
            self.assertGreaterEqual(event["duration_ms"], 0)
        self.assertEqual({event["route"] for event in events}, {"/ask", "/claims/{claim_id}", "unmatched"})

    def test_metrics_require_auth_and_ignore_health_and_metrics_polling(self):
        before = app.state.telemetry.snapshot()
        self.assertEqual(self.client.get("/ops/metrics").status_code, 401)
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        response = self.client.get("/ops/metrics", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), before)
        self.assertEqual(app.state.telemetry.snapshot(), before)

    def test_metrics_count_success_validation_auth_and_database_errors(self):
        def count(status):
            return sum(row["count"] for row in app.state.telemetry.snapshot()["metrics"]
                       if row["route"] == "/claims/{claim_id}" and row["method"] == "GET" and row["status_code"] == status)
        before = {status: count(status) for status in (200, 401, 422, 503)}
        self.client.get("/claims/RK-1042", auth=self.auth)
        self.client.get("/claims/RK-1042")
        self.client.get("/claims/not-valid", auth=self.auth)
        with patch("claims.lookup_demo_claim", side_effect=ClaimServiceUnavailable()):
            self.client.get("/claims/RK-1042", auth=self.auth)
        for status in before:
            self.assertEqual(count(status), before[status] + 1)

    def test_unexpected_error_is_generic_without_exception_logging(self):
        with self.assertLogs("support.requests", level="INFO") as captured:
            with patch("claims.lookup_demo_claim", side_effect=RuntimeError("fake-private-connection-string")):
                response = self.client.get("/claims/RK-1042", auth=self.auth)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Internal service error"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("fake-private-connection-string", "\n".join(captured.output))
        self.assertEqual(json.loads(captured.records[-1].getMessage())["status_code"], 500)

    def test_parallel_records_use_bounded_labels_without_lost_counts(self):
        from concurrent.futures import ThreadPoolExecutor
        telemetry = RequestTelemetry()
        def write(index):
            telemetry.record(request_id=f"{index:032x}", route=f"/private/{index}", method="UNUSUAL", status_code=200, duration_ms=1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(100)))
        rows = telemetry.snapshot()["metrics"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["route"], "unmatched")
        self.assertEqual(rows[0]["method"], "OTHER")
        self.assertEqual(rows[0]["count"], 100)
        self.assertEqual(rows[0]["duration_ms_total"], 100)
