"""Offline boundaries for the local MCP-to-REST bridge, without API/model calls."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from mcp_claims import TravelBridge, load_credentials


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def bridge(self, handler):
        bridge = TravelBridge("gandalf", "fake-password", transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(bridge.close)
        return bridge

    async def test_invalid_arguments_never_reach_http_and_are_not_echoed(self):
        def unexpected(request):
            self.fail("Invalid tool inputs must not reach HTTP")
        bridge = await self.bridge(unexpected)
        invalid = [
            ("get_claim_status", {"claim_id": "RK-1042", "owner": "fake-secret-owner"}),
            ("get_claim_status", {"claim_id": "RK-1042", "password": "fake-secret-password"}),
            ("get_claim_status", {"claim_id": "RK-1042' OR 1=1"}),
            ("get_claim_status", {"claim_id": 1042}),
            ("get_claim_status", {}),
            ("ask_travel_question", {"question": " "}),
            ("ask_travel_question", {"question": "a" * 2001}),
            ("ask_travel_question", {"question": [], "url": "https://untrusted.invalid"}),
        ]
        for name, arguments in invalid:
            with self.subTest(name=name, arguments=arguments):
                result = await bridge.call(name, arguments)
                self.assertTrue(result.is_error)
                self.assertEqual(result.content[0].text, "Invalid tool arguments.")
        self.assertTrue((await bridge.call("run_sql", {"sql": "SELECT *"})).is_error)

    async def test_auth_is_bound_and_destination_is_fixed(self):
        def handle(request):
            self.assertEqual(str(request.url), "http://127.0.0.1:8018/ask")
            self.assertEqual(request.headers["authorization"], "Basic Z2FuZGFsZjpmYWtlLXBhc3N3b3Jk")
            self.assertEqual(json.loads(request.content), {"question": "Wie ist der Status?"})
            return httpx.Response(200, json={"status": "clarify", "answer": "Bitte Vorgangsnummer angeben.", "sources": []})
        result = await (await self.bridge(handle)).call("ask_travel_question", {"question": "Wie ist der Status?"})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["status"], "clarify")

    async def test_missing_claim_is_generic_and_does_not_echo_upstream_body(self):
        bridge = await self.bridge(lambda request: httpx.Response(404, text="fake-private-debug-info"))
        result = await bridge.call("get_claim_status", {"claim_id": "RK-1042"})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, {"status": "not_found", "claim": None})

    async def test_only_valid_upstream_request_ids_are_metadata(self):
        for header in ("b" * 32, "fake-sensitive-header", ""):
            with self.subTest(header=header):
                bridge = await self.bridge(lambda request: httpx.Response(404, headers={"X-Request-ID": header}))
                result = await bridge.call("get_claim_status", {"claim_id": "RK-1042"})
                if header == "b" * 32:
                    self.assertEqual(result.meta, {"support/request_id": header})
                else:
                    self.assertIsNone(result.meta)
                self.assertEqual(result.structured_content, {"status": "not_found", "claim": None})
        bridge = await self.bridge(lambda request: httpx.Response(503, headers={"X-Request-ID": "c" * 32}))
        error = await bridge.call("get_claim_status", {"claim_id": "RK-1042"})
        self.assertTrue(error.is_error)
        self.assertEqual(error.meta, {"support/request_id": "c" * 32})

    async def test_http_errors_and_redirects_are_generic(self):
        for status in (401, 403, 422, 500, 503, 302):
            with self.subTest(status=status):
                requests = []
                def handle(request):
                    requests.append(request)
                    return httpx.Response(status, text="fake-sensitive-body", headers={"Location": "https://untrusted.invalid"})
                bridge = await self.bridge(handle)
                result = await bridge.call("get_claim_status", {"claim_id": "RK-1042"})
                self.assertTrue(result.is_error)
                self.assertNotIn("fake-sensitive-body", str(result.content))
                self.assertEqual(len(requests), 1)

    async def test_connection_error_is_not_exposed(self):
        def handle(request):
            raise httpx.ConnectError("fake-password /private/backend.sqlite", request=request)
        result = await (await self.bridge(handle)).call("get_claim_status", {"claim_id": "RK-1042"})
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, "Travel service unavailable. Try again later.")

    async def test_invalid_upstream_json_schema_and_size_are_rejected(self):
        replies = [
            httpx.Response(200, text="not JSON fake-sensitive"),
            httpx.Response(200, json={"status": "answered", "answer": "okay", "sources": [], "password": "fake-sensitive"}),
            httpx.Response(200, text="x" * 32769),
        ]
        for response in replies:
            with self.subTest(response=response.status_code):
                bridge = await self.bridge(lambda request: response)
                result = await bridge.call("ask_travel_question", {"question": "Minibar?"})
                self.assertTrue(result.is_error)
                self.assertEqual(result.content[0].text, "Travel service returned an invalid response.")


class CredentialTests(unittest.TestCase):
    def test_credentials_require_owner_only_file_and_exact_fields(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "credentials.json"
            path.write_text(json.dumps({"username": "gandalf", "password": "fake-password"}))
            path.chmod(0o600)
            self.assertEqual(load_credentials(path), ("gandalf", "fake-password"))
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                load_credentials(path)
            path.chmod(0o600)
            path.write_text(json.dumps({"username": "gandalf", "password": "fake-password", "url": "https://untrusted.invalid"}))
            with self.assertRaises(ValueError):
                load_credentials(path)
