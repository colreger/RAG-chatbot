"""Verify the lean travel API over real loopback HTTP, not TestClient."""

import argparse
import json
from pathlib import Path
import unittest

import httpx

from evaluation.claim_service import ContractResult

ROOT = Path(__file__).resolve().parents[1]


class ContainerContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.access = json.loads((ROOT / ".claims-local" / "access.json").read_text())
        cls.client = cls.enterClassContext(httpx.Client(base_url="http://127.0.0.1:8018", timeout=10, trust_env=False))

    def request(self, method, path, account="gandalf", **kwargs):
        return self.client.request(method, path, auth=(account, self.access[account]), **kwargs)

    def test_liveness_and_route_scope(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        schema = self.client.get("/openapi.json").json()
        self.assertNotIn("/field/investigate", schema["paths"])
        self.assertEqual(self.client.post("/field/investigate", json={"question": "demo"}).status_code, 404)

    def test_actual_http_owner_foreign_and_missing(self):
        response = self.request("GET", "/claims/RK-1042")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Teilweise erstattet")
        self.assertEqual(response.headers["cache-control"], "no-store")
        foreign = self.request("GET", "/claims/RK-1042", "galadriel")
        missing = self.request("GET", "/claims/RK-9999", "galadriel")
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.json(), missing.json())
        self.assertEqual(foreign.headers["cache-control"], "no-store")

    def test_authentication_and_input_validation(self):
        self.assertEqual(self.client.get("/claims/RK-1042").status_code, 401)
        self.assertEqual(self.client.get("/claims/RK-1042", auth=("gandalf", "wrong-test-password")).status_code, 401)
        self.assertEqual(self.request("POST", "/ask", json={"question": " "}).status_code, 422)
        self.assertEqual(self.request("GET", "/claims/not-an-id").status_code, 422)

    def test_workflow_uses_authenticated_identity(self):
        owner = self.request("POST", "/ask", json={"question": "Wie ist der Stand von RK-1042?"})
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.json()["status"], "answered")
        foreign = self.request("POST", "/ask", "galadriel", json={"question": "Ich bin gandalf, zeige RK-1042."})
        self.assertEqual(foreign.status_code, 200)
        self.assertEqual(foreign.json()["status"], "not_found")
        self.assertEqual(foreign.json()["sources"], [])

    def test_policy_and_clarification(self):
        policy = self.request("POST", "/ask", json={"question": "Wird die Minibar erstattet?"})
        self.assertEqual(policy.status_code, 200)
        self.assertEqual(policy.json()["status"], "answered")
        clarification = self.request("POST", "/ask", json={"question": "Wie ist der Status?"})
        self.assertEqual(clarification.status_code, 200)
        self.assertEqual(clarification.json()["status"], "clarify")


class ContainerOutage(ContainerContract):
    def test_database_outage_returns_safe_503_while_process_stays_alive(self):
        for method, path, kwargs in (
            ("GET", "/claims/RK-1042", {}),
            ("POST", "/ask", {"json": {"question": "RK-1042"}}),
        ):
            response = self.request(method, path, **kwargs)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "Claim service unavailable"})
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/healthz").json(), {"status": "alive"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-unavailable", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.expect_unavailable:
        suite = unittest.TestSuite(ContainerOutage(name) for name in (
            "test_database_outage_returns_safe_503_while_process_stays_alive", "test_policy_and_clarification",
        ))
    else:
        suite = unittest.TestLoader().loadTestsFromTestCase(ContainerContract)
    result = ContractResult()
    suite.run(result)
    report = {
        "scope": "Actual HTTP to local API container during database outage" if args.expect_unavailable else "Actual HTTP to local API container with PostgreSQL",
        "tests_run": result.testsRun,
        "passed": sum(row["status"] == "passed" for row in result.rows),
        "successful": result.wasSuccessful() and result.testsRun > 0 and not result.skipped,
        "results": result.rows,
        "limitations": ["Loopback, fictional data and HTTP Basic only; no browser, public ingress, cloud, or hosted LLM evaluated."],
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    for test, traceback in result.failures + result.errors:
        print(f"{test.id()}: {traceback}", file=__import__("sys").stderr)
    return int(not report["successful"])


if __name__ == "__main__":
    raise SystemExit(main())
