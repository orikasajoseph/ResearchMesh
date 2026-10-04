import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests.http_server import local_server


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server_context = local_server()
        cls.base_url = cls.server_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.server_context.__exit__(None, None, None)

    def request(self, path, payload=None, raw=None):
        body = raw if raw is not None else (
            json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        )
        request = Request(self.base_url + path, data=body, headers={"Content-Type": "application/json"})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_is_explicit_about_mock_mode(self):
        status, response = self.request("/health")
        self.assertEqual(status, 200)
        self.assertTrue(response["healthy"])
        self.assertTrue(response["demoOnly"])
        for field in ("realModelConnected", "realAgentsConnected", "aipConnected"):
            self.assertFalse(response[field])

    def test_all_three_cases_work_over_real_http(self):
        status, cases = self.request("/demo/cases")
        self.assertEqual(status, 200)
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case["name"]):
                status, response = self.request("/plan", {"userQuery": case["userQuery"]})
                self.assertEqual(status, 200)
                self.assertTrue(response["demoOnly"])
                self.assertEqual(response["plannerMode"], "fixture")
                self.assertEqual(response["plan"]["userQuery"], case["userQuery"])
        self.assertTrue(response["plan"]["needsClarification"])
        self.assertEqual(response["plan"]["tasks"], [])

    def test_new_queries_are_not_given_fake_plans(self):
        status, response = self.request("/plan", {"userQuery": "一个全新的研究课题"})
        self.assertEqual(status, 409)
        self.assertEqual(response["detail"]["code"], "MOCK_QUERY_NOT_SUPPORTED")

    def test_invalid_requests_are_rejected(self):
        for payload in ({}, {"userQuery": " "}, {"userQuery": 123},
                        {"userQuery": "字" * 6001}, {"userQuery": "测试", "status": "done"}):
            with self.subTest(payload_type=str(type(payload))):
                status, _ = self.request("/plan", payload)
                self.assertEqual(status, 422)
        status, _ = self.request("/plan", raw=b"{invalid-json")
        self.assertEqual(status, 422)

    def test_openapi_lists_local_endpoints(self):
        status, response = self.request("/openapi.json")
        self.assertEqual(status, 200)
        self.assertIn("/plan", response["paths"])
        self.assertIn("/health", response["paths"])
        self.assertFalse(response["components"]["schemas"]["PlanRequest"]["additionalProperties"])
