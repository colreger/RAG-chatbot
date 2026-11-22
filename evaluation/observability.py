"""Verify actual loopback telemetry and the dedicated API container's logs."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import unittest
from uuid import uuid4

import httpx

from evaluation.claim_service import ContractResult

ROOT = Path(__file__).resolve().parents[1]


class LiveObservabilityTests(unittest.TestCase):
    expected_status = 200

    @classmethod
    def setUpClass(cls):
        cls.access = json.loads((ROOT / ".claims-local/access.json").read_text())
        cls.client = cls.enterClassContext(httpx.Client(base_url="http://127.0.0.1:8018", timeout=10, trust_env=False))
        cls.auth = ("gandalf", cls.access["gandalf"])

    def metrics(self):
        response = self.client.get("/ops/metrics", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def test_metrics_and_ids_for_real_claim_request(self):
        def count(snapshot):
            return sum(row["count"] for row in snapshot["metrics"] if row["route"] == "/ask"
                       and row["method"] == "POST" and row["status_code"] == self.expected_status)
        before = self.metrics()
        response = self.client.post("/ask", auth=self.auth, json={"question": "RK-1042"}, headers={"X-Request-ID": "a" * 32})
        self.assertEqual(response.status_code, self.expected_status)
        self.assertRegex(response.headers["x-request-id"], r"^[a-f0-9]{32}$")
        self.assertNotEqual(response.headers["x-request-id"], "a" * 32)
        self.assertEqual(response.headers["cache-control"], "no-store")
        after = self.metrics()
        self.assertEqual(count(after), count(before) + 1)
        for row in after["metrics"]:
            self.assertIn(row["route"], ("/claims/{claim_id}", "/ask", "/field/investigate", "unmatched"))
            self.assertGreaterEqual(row["duration_ms_total"], 0)
            self.assertNotIn("request_id", row)

    def test_ops_auth_and_health_do_not_change_counters(self):
        before = self.metrics()
        self.assertEqual(self.client.get("/ops/metrics").status_code, 401)
        self.assertEqual(self.client.get("/healthz").json(), {"status": "alive"})
        self.assertEqual(self.metrics(), before)

    def test_real_container_logs_are_correlated_and_content_free(self):
        marker = "private-observability-check-" + uuid4().hex
        answered = self.client.post("/ask", auth=self.auth, params={"private_query": marker},
                                    headers={"X-Private-Header": marker}, json={"question": marker + " RK-1042"})
        self.assertEqual(answered.status_code, self.expected_status)
        missing = self.client.get("/" + marker, auth=self.auth)
        self.assertEqual(missing.status_code, 404)
        container = os.environ.get("CLAIMS_TEST_CONTAINER", "support-claims-learning-api-1")
        completed = subprocess.run(["docker", "logs", container], capture_output=True, text=True, check=True)
        logs = completed.stdout + completed.stderr
        self.assertNotIn(marker, logs)
        for password in self.access.values():
            self.assertNotIn(password, logs)
        events = []
        for line in logs.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("event") == "http_request":
                events.append(event)
        expected = {answered.headers["x-request-id"], missing.headers["x-request-id"]}
        actual = {event["request_id"] for event in events}
        self.assertTrue(expected <= actual)
        for event in events:
            self.assertEqual(set(event), {"event", "timestamp", "request_id", "route", "method", "status_code", "duration_ms"})
            self.assertNotIn("RK-1042", json.dumps(event))
            self.assertNotIn("gandalf", json.dumps(event))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-unavailable", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    LiveObservabilityTests.expected_status = 503 if args.expect_unavailable else 200
    result = ContractResult()
    unittest.TestLoader().loadTestsFromTestCase(LiveObservabilityTests).run(result)
    report = {
        "scope": "Actual container request telemetry during database outage" if args.expect_unavailable else "Actual container request telemetry",
        "tests_run": result.testsRun,
        "passed": sum(row["status"] == "passed" for row in result.rows),
        "successful": result.wasSuccessful() and result.testsRun > 0 and not result.skipped,
        "results": result.rows,
        "limits": ["Process-local HTTP telemetry only; no retention service, alerting, full distributed tracing, hosted LLM or model-quality monitoring."],
    }
    serialized = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized)
    print(serialized, end="")
    for test, traceback in result.failures + result.errors:
        print(f"{test.id()}: {traceback}", file=__import__("sys").stderr)
    return int(not report["successful"])


if __name__ == "__main__":
    raise SystemExit(main())
