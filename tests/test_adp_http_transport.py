"""Offline tests for the no-retry platform Discovery HTTP transport."""
import json
import unittest

import httpx

from resolver.adp import AdpAgentResolver, AdpResolverError
from resolver.discovery_http import DiscoveryHttpTransport, DISCOVERY_PATH
from tests.test_week2_completion import adp_fixture, MockExecutor


class DiscoveryHttpTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_resolver_shape_uses_one_post_and_selects_candidate(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=adp_fixture())
        client = httpx.AsyncClient(base_url="https://discovery.example",
                                   transport=httpx.MockTransport(handler))
        transport = DiscoveryHttpTransport("https://discovery.example", client=client)
        try:
            resolver = AdpAgentResolver(transport, MockExecutor)
            handle = await resolver.resolve("literature.search")
        finally:
            await client.aclose()
        self.assertEqual(handle.agent_id, "offline-literature")
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), "https://discovery.example" + DISCOVERY_PATH)
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["filter"]["conditions"][1]["value"], "literature.search")
        self.assertEqual(transport.records[0]["attempts"], 1)
        self.assertEqual(transport.records[0]["httpStatus"], 200)
        self.assertNotIn("acsMap", json.dumps(transport.records))

    async def test_server_error_invalid_json_and_network_failure_are_bounded(self):
        cases = (
            (lambda _r: httpx.Response(503, text="private-secret"), "ADP_SERVER_ERROR"),
            (lambda _r: httpx.Response(200, text="not-json-private-secret"), "ADP_INVALID_RESPONSE"),
            (lambda request: (_ for _ in ()).throw(httpx.ConnectError("private-secret", request=request)),
             "ADP_TRANSPORT_ERROR"),
        )
        for handler, code in cases:
            with self.subTest(code=code):
                client = httpx.AsyncClient(base_url="https://discovery.example",
                                           transport=httpx.MockTransport(handler))
                transport = DiscoveryHttpTransport("https://discovery.example", client=client)
                try:
                    with self.assertRaisesRegex((AdpResolverError, ValueError), code):
                        await AdpAgentResolver(transport, MockExecutor).resolve("literature.search")
                finally:
                    await client.aclose()
                self.assertEqual(len(transport.records), 1)
                self.assertNotIn("private-secret", json.dumps(transport.records))

    def test_rejects_unsafe_or_ambiguous_urls(self):
        for value in ("http://wt.ioa.pub/discovery", "https://user:pass@wt.ioa.pub/discovery",
                      "https://wt.ioa.pub/discovery?token=secret", "not-a-url"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "INVALID_DISCOVERY_URL"):
                DiscoveryHttpTransport(value)

    def test_rejects_incomplete_mtls_or_invalid_timeout_without_network(self):
        with self.assertRaisesRegex(ValueError, "INCOMPLETE_DISCOVERY_MTLS_CONFIGURATION"):
            DiscoveryHttpTransport("https://wt.ioa.pub/discovery", cert_file=__file__)
        for value in (True, 0, float("nan")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "INVALID_DISCOVERY_HTTP_TIMEOUT"):
                DiscoveryHttpTransport("https://wt.ioa.pub/discovery", timeout_seconds=value)


if __name__ == "__main__":
    unittest.main()
