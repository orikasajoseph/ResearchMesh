"""实际 SDK + HTTPX mock 验证请求、gate、脱敏；不联网，不读取真实 Key。"""

import asyncio
from datetime import datetime
import json
import socket
import ssl
from types import SimpleNamespace
import time
import unittest

import httpx

from semantic_http import (GLOBAL_RATE_GATE, ProcessRateGate, ObservedRequester,
                           create_monitored_client, client_diagnostics, transport_failure)


class SemanticHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_attaches_key_and_keeps_relevance_filters_single_request(self):
        received = []

        def handler(request):
            received.append(request)
            return httpx.Response(200, json={"total": 1383267, "data": [{"paperId": "p", "title": "Paper"}]})

        client = create_monitored_client(SimpleNamespace(api_key="safe-test-key", timeout_seconds=2),
                                          source="run_research", subtask_id="S1",
                                          transport=httpx.MockTransport(handler), gate=ProcessRateGate(.001))
        result = await client.search_paper(query="world model", year="2024:2026", limit=3,
                                           fields=["paperId", "title"])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].headers["x-api-key"], "safe-test-key")
        self.assertEqual(received[0].url.path, "/graph/v1/paper/search")
        self.assertEqual(received[0].url.params["year"], "2024:2026")
        self.assertEqual(received[0].url.params["limit"], "3")
        self.assertEqual(len(result.items), 1)
        diag = client_diagnostics(client, "safe-test-key")
        self.assertTrue(diag["apiKeyConfigured"])
        self.assertTrue(diag["apiKeyAttached"])
        self.assertNotIn("safe-test-key", json.dumps(diag))
        self.assertEqual(diag["httpRequests"][0]["httpStatus"], 200)

    async def test_429_headers_and_body_are_redacted(self):
        def handler(request):
            return httpx.Response(429, headers={"Retry-After": "3", "X-RateLimit-Remaining": "0",
                                                "Authorization": "safe-test-key", "Set-Cookie": "secret"},
                                  json={"message": "limited safe-test-key"})

        requester = ObservedRequester(2, "safe-test-key", transport=httpx.MockTransport(handler),
                                      gate=ProcessRateGate(.001))
        with self.assertRaises(ConnectionRefusedError):
            await requester.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search",
                                           "query=world+model&limit=1", {"x-api-key": "safe-test-key"})
        event = requester.events[0]
        self.assertEqual(event["httpStatus"], 429)
        self.assertEqual(event["retryAfterHeader"], "3")
        self.assertEqual(event["rateLimitHeaders"]["x-ratelimit-remaining"], "0")
        self.assertNotIn("safe-test-key", json.dumps(event))
        self.assertNotIn("authorization", event["rateLimitHeaders"])
        self.assertNotIn("set-cookie", event["rateLimitHeaders"])

    async def test_single_request_budget_blocks_second_send_even_after_429(self):
        sent = []
        def handler(request):
            sent.append(request)
            return httpx.Response(429, json={"message": "limited"})
        requester = ObservedRequester(2, "safe-test-key", transport=httpx.MockTransport(handler),
                                      gate=ProcessRateGate(.001), max_requests=1)
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        with self.assertRaises(ConnectionRefusedError):
            await requester.get_data_async(url, "query=QLoRA&limit=1", {"x-api-key": "safe-test-key"})
        with self.assertRaisesRegex(RuntimeError, "REQUEST_BUDGET_EXHAUSTED"):
            await requester.get_data_async(url, "query=QLoRA&limit=1", {"x-api-key": "safe-test-key"})
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(requester.events), 1)

    async def test_configured_key_is_not_same_as_actual_attachment(self):
        requester = ObservedRequester(2, "safe-test-key", transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={})), gate=ProcessRateGate(.001))
        await requester.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search", "limit=1", {})
        self.assertTrue(requester.events[0]["apiKeyConfigured"])
        self.assertFalse(requester.events[0]["apiKeyAttached"])

    async def test_different_clients_and_endpoints_share_one_gate(self):
        gate = ProcessRateGate(.02)
        starts = []

        def handler(request):
            starts.append(time.monotonic())
            return httpx.Response(200, json={})

        a = ObservedRequester(2, None, transport=httpx.MockTransport(handler), gate=gate)
        b = ObservedRequester(2, None, transport=httpx.MockTransport(handler), gate=gate)
        await asyncio.gather(a.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search", "limit=1", {}),
                             b.get_data_async("https://api.semanticscholar.org/graph/v1/paper/test", "", {}))
        self.assertGreaterEqual(starts[1] - starts[0], .018)
        events = gate.snapshot()
        self.assertEqual([e["requestSequence"] for e in events], [1, 2])
        self.assertIsNotNone(events[1]["previousRequestAt"])
        self.assertGreater(events[1]["rateGateWaitMs"], 0)
        self.assertEqual(GLOBAL_RATE_GATE.interval, 1.2)

    async def test_cross_thread_event_loops_share_one_gate(self):
        gate = ProcessRateGate(.02)
        starts = []

        async def worker():
            requester = ObservedRequester(2, None, transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={})), gate=gate)
            await requester.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search", "", {})

        await asyncio.gather(asyncio.to_thread(lambda: asyncio.run(worker())),
                             asyncio.to_thread(lambda: asyncio.run(worker())))
        events = gate.snapshot()
        times = [datetime.fromisoformat(e["actualRequestAt"]).timestamp() for e in events]
        self.assertEqual(len(events), 2)
        self.assertGreaterEqual(times[1] - times[0], .018)

    async def test_cancelled_request_releases_gate_and_logs(self):
        gate = ProcessRateGate(.001)

        async def slow(request):
            await asyncio.sleep(1)
            return httpx.Response(200, json={})

        requester = ObservedRequester(2, None, transport=httpx.MockTransport(slow), gate=gate)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(requester.get_data_async(
                "https://api.semanticscholar.org/graph/v1/paper/search", "", {}), .03)
        second = ObservedRequester(2, None, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), gate=gate)
        await second.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search", "", {})
        self.assertEqual(len(gate.snapshot()), 2)

    async def test_non_official_host_never_receives_key(self):
        requester = ObservedRequester(2, "safe-test-key", gate=ProcessRateGate(.001))
        with self.assertRaisesRegex(ValueError, "ENDPOINT_REJECTED"):
            await requester.get_data_async("https://example.invalid/graph/v1/paper/search", "", {})
        self.assertEqual(requester.events, [])

    async def test_network_error_does_not_echo_sensitive_exception(self):
        def handler(request):
            raise httpx.ConnectError("safe-test-key", request=request)

        requester = ObservedRequester(2, "safe-test-key", transport=httpx.MockTransport(handler), gate=ProcessRateGate(.001))
        with self.assertRaisesRegex(RuntimeError, "^SEMANTIC_SCHOLAR_TRANSPORT_FAILED$"):
            await requester.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search", "", {})
        self.assertNotIn("safe-test-key", json.dumps(requester.events))
        self.assertEqual(requester.events[-1]["transportError"], "CONNECT_ERROR")
        self.assertTrue(requester.events[-1]["transportRetryable"])

    async def test_timeout_phase_and_effective_timeout_logged_without_secrets(self):
        for error, reason in ((httpx.ConnectTimeout, "CONNECT_TIMEOUT"), (httpx.ReadTimeout, "READ_TIMEOUT")):
            def handler(request):
                raise error("safe-test-key proxy credentials", request=request)
            requester = ObservedRequester(20, "safe-test-key", transport=httpx.MockTransport(handler), gate=ProcessRateGate(.001))
            with self.assertRaises(RuntimeError):
                await requester.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search", "", {})
            event = requester.events[-1]
            self.assertEqual(event["transportError"], reason)
            self.assertEqual(event["requestTimeouts"]["connect"], 20)
            self.assertTrue(event["transportRetryable"])
            self.assertNotIn("safe-test-key", json.dumps(event))

    async def test_tls_proxy_unknown_errors_not_marked_retryable(self):
        tls = httpx.ConnectError("safe-test-key")
        tls.__cause__ = ssl.SSLCertVerificationError("certificate failure safe-test-key")
        for error, reason in ((tls, "TLS_CERTIFICATE_ERROR"), (httpx.ProxyError("private"), "PROXY_ERROR"),
                              (RuntimeError("private"), "OTHER_TRANSPORT_ERROR")):
            event = transport_failure(error)
            self.assertEqual(event["transportError"], reason)
            self.assertFalse(event["transportRetryable"])
            self.assertNotIn("private", json.dumps(event)); self.assertNotIn("safe-test-key", json.dumps(event))

    async def test_dns_failure_has_safe_classification(self):
        error = httpx.ConnectError("private")
        error.__cause__ = socket.gaierror(-3, "private DNS text")
        event = transport_failure(error)
        self.assertEqual(event["transportError"], "DNS_ERROR")
        self.assertTrue(event["transportRetryable"])


if __name__ == "__main__":
    unittest.main()
