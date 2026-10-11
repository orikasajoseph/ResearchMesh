import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from input_binding import bind_task_input, InputBindingError
from literature_adapter import AdapterError, call_wrapper, normalize_response, to_wrapper_request, verify_replay_request
from orchestrator import make_task_payload
from run_literature import select_task, render_literature_report


def plan():
    return json.loads((Path(__file__).resolve().parents[1] / "examples/literature_alphaevolve_v02.json").read_text(encoding="utf-8"))


def response():
    return {"schemaVersion": "paper_records.v1", "requestId": "test-request", "status": "success",
            "papers": [{"paperId": "test-paper", "title": "Test paper", "authors": [{"name": "Test Author"}],
                        "year": 2025, "doi": "10.1234/test", "url": "https://example.invalid/paper",
                        "pdfUrl": None, "repositoryUrls": ["https://arxiv.org/abs/2506.13131"]}],
            "total": 1, "provider": "semantic-scholar",
            "metrics": {"durationMs": 1.0, "attempts": 1, "cacheHit": False}, "error": None}


class LiteratureAdapterTests(unittest.TestCase):
    def setUp(self):
        self.payload = make_task_payload(plan()["tasks"][0])
        self.request = to_wrapper_request(self.payload, "test-request")

    def normalize(self, raw=None):
        return normalize_response(self.payload, self.request, raw or response())

    def test_request_mapping(self):
        self.assertEqual(self.request["limit"], 5)
        self.assertEqual(self.request["schemaVersion"], "literature_search.v0.1")
        for field in ("requiredSkill", "dependsOn", "subtaskId", "taskType"):
            self.assertNotIn(field, self.request)

    def test_year_ranges(self):
        for constraints, expected in [({"yearFrom": 2023, "yearTo": 2025}, "2023:2025"),
                                      ({"yearFrom": 2025}, "2025:"), ({"yearTo": 2025}, ":2025"),
                                      ({"yearFrom": 2025, "yearTo": 2025}, "2025")]:
            self.payload["constraints"] = constraints
            self.assertEqual(to_wrapper_request(self.payload, "id")["filters"]["year"], expected)

    def test_unsupported_constraint_not_silently_dropped(self):
        self.payload["constraints"]["openAccessOnly"] = True
        with self.assertRaisesRegex(AdapterError, "UNSUPPORTED_CONSTRAINT"):
            to_wrapper_request(self.payload, "id")

    def test_invalid_request(self):
        self.payload["constraints"]["maxResults"] = True
        with self.assertRaises(AdapterError):
            to_wrapper_request(self.payload, "id")

    def test_non_search_rejected(self):
        self.payload["taskType"] = "paper_read"
        with self.assertRaisesRegex(AdapterError, "UNSUPPORTED_TASK"):
            to_wrapper_request(self.payload, "id")

    def test_source_links_are_not_code_repositories(self):
        result = self.normalize()["result"]
        data = result["items"][0]["data"]
        self.assertEqual(data["repositoryUrls"], [])
        self.assertEqual(data["sourceUrls"], ["https://arxiv.org/abs/2506.13131"])
        self.assertEqual(data["authors"], ["Test Author"])
        self.assertIsNone(data["pdfUrl"])
        self.assertNotIn("status", result)

    def test_explicit_github_repo_preserved(self):
        raw = response()
        raw["papers"][0]["repositoryUrls"].append("https://github.com/example/repo")
        self.assertEqual(self.normalize(raw)["result"]["items"][0]["data"]["repositoryUrls"],
                         ["https://github.com/example/repo"])

    def test_input_not_mutated(self):
        raw = response()
        before = deepcopy(raw)
        self.normalize(raw)
        self.assertEqual(raw, before)

    def test_correlation_rejected(self):
        raw = response(); raw["requestId"] = "other"
        with self.assertRaisesRegex(AdapterError, "REQUEST_ID_MISMATCH"):
            self.normalize(raw)

    def test_request_tampering_rejected(self):
        self.request["query"] = "another query"
        with self.assertRaisesRegex(AdapterError, "REQUEST_TASK_MISMATCH"):
            self.normalize()

    def test_empty_search_is_empty_result_with_warning(self):
        raw = response(); raw.update(status="failure", papers=[], total=0,
                                      error={"code": "NO_RESULTS", "message": "empty", "retryable": False})
        out = self.normalize(raw)
        self.assertEqual(out["result"]["items"], [])
        self.assertEqual(out["diagnostics"]["outcome"], "empty")
        self.assertIsNone(out["error"])

    def test_provider_errors_do_not_echo_secret_messages(self):
        for code in ("RATE_LIMITED", "PROVIDER_UNAVAILABLE", "INVALID_REQUEST", "unknown"):
            raw = response(); raw.update(status="failure", papers=[], total=0,
                                          error={"code": code, "message": "secret-credential", "retryable": True})
            out = self.normalize(raw)
            self.assertIsNone(out["result"])
            self.assertNotIn("secret-credential", json.dumps(out))

    def test_contradictory_result_rejected(self):
        for update in ({"status": "failure"}, {"papers": []}, {"total": 0},
                       {"error": {"code": "NO_RESULTS", "message": "x"}}):
            raw = response(); raw.update(update)
            with self.subTest(update=update), self.assertRaises(AdapterError):
                self.normalize(raw)

    def test_malformed_metrics(self):
        raw = response(); raw["metrics"]["durationMs"] = float("nan")
        with self.assertRaisesRegex(AdapterError, "INVALID_WRAPPER_RESULT"):
            self.normalize(raw)

    def test_limit_and_duplicates(self):
        raw = response(); raw["papers"] *= 3; raw["total"] = 3
        self.assertEqual(len(self.normalize(raw)["result"]["items"]), 1)
        raw["papers"] = [deepcopy(response()["papers"][0]) for _ in range(6)]
        raw["total"] = 6
        for i, p in enumerate(raw["papers"]): p["doi"] = f"10.1234/{i}"
        self.assertEqual(len(self.normalize(raw)["result"]["items"]), 5)

    def test_year_filter_enforced_on_return(self):
        self.payload["constraints"].update(yearFrom=2023, yearTo=2025)
        self.request = to_wrapper_request(self.payload, "test-request")
        for year in (2020, None):
            raw = response(); raw["papers"][0]["year"] = year
            with self.assertRaisesRegex(AdapterError, "NO_VALID_PAPER_RECORDS"):
                self.normalize(raw)

    def test_invalid_paper_does_not_hide_good_results(self):
        raw = response(); raw["papers"].append({"paperId": "bad", "title": None}); raw["total"] = 2
        self.assertEqual(self.normalize(raw)["diagnostics"]["droppedCount"], 1)

    def test_reader_binding_preserves_locator(self):
        task = {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read", "goal": "read",
                "input": {"papers": []}, "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1",
                "requiredSkill": "paper.analyze", "dependsOn": ["S1"]}
        bound = bind_task_input(task, {"S1": self.normalize()["result"]})
        self.assertEqual(bound["input"]["papers"][0]["doi"], "10.1234/test")
        self.assertEqual(bound["input"]["papers"][0]["repositoryUrls"], [])

    def test_repository_task_not_given_arxiv_as_repo(self):
        task = {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "repository_analyze", "goal": "analyze",
                "input": {"papers": [], "repositoryUrls": [], "paperNotes": []},
                "constraints": {"allowRepositorySearch": False, "executeCode": False},
                "outputSchema": "code_records.v1", "requiredSkill": "repository.analyze", "dependsOn": ["S1"]}
        with self.assertRaises(InputBindingError):
            bind_task_input(task, {"S1": self.normalize()["result"]})

    def test_replay_match(self):
        fixture = deepcopy(self.request)
        fixture["filters"].update(openAccessOnly=False, venues=[])
        verify_replay_request(self.request, fixture)

    def test_replay_mismatch(self):
        for key, value in (("query", "different"), ("limit", 1), ("requestId", "other"),
                           ("filters", {"openAccessOnly": True})):
            fixture = deepcopy(self.request); fixture[key] = value
            with self.assertRaisesRegex(AdapterError, "REPLAY_QUERY_OR_FILTER_MISMATCH"):
                verify_replay_request(self.request, fixture)

    def test_plan_selection_rejects_partial_execution(self):
        candidate = plan(); candidate["tasks"].append(deepcopy(candidate["tasks"][0])); candidate["tasks"][1]["subtaskId"] = "S2"
        with self.assertRaisesRegex(AdapterError, "ONLY_STANDALONE"):
            select_task(candidate)

    def test_report_is_honest_about_replay(self):
        text = render_literature_report(plan(), self.normalize(), "replay")
        self.assertIn("历史样例回放", text)
        self.assertIn("未调用 LLM、ADP 或 AIP", text)
        self.assertIn("代码仓库：未确认", text)


class WrapperCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_injected_agent_is_called_with_model(self):
        class Request:
            @staticmethod
            def model_validate(data): return SimpleNamespace(**data)
        class Agent:
            async def literature_search(self, request):
                raw = response(); raw["requestId"] = request.requestId
                return SimpleNamespace(model_dump=lambda **kw: raw)
        result = await call_wrapper(select_task(plan()), "new-request", Agent(), Request)
        self.assertEqual(result["diagnostics"]["requestId"], "new-request")

    async def test_timeout(self):
        class Agent:
            async def literature_search(self, request): await asyncio.sleep(1)
        with self.assertRaisesRegex(AdapterError, "WRAPPER_TIMEOUT"):
            await call_wrapper(select_task(plan()), "id", Agent(), SimpleNamespace(model_validate=lambda d: d), timeout_seconds=.001)

    async def test_exception_redacted(self):
        class Agent:
            async def literature_search(self, request): raise RuntimeError("private-key")
        with self.assertRaisesRegex(AdapterError, "^WRAPPER_CALL_FAILED$"):
            await call_wrapper(select_task(plan()), "id", Agent(), SimpleNamespace(model_validate=lambda d: d))


class SDKProductTests(unittest.TestCase):
    def test_normalized_business_fits_official_product(self):
        from acps_sdk.aip import Product, StructuredDataItem
        payload = select_task(plan())
        request = to_wrapper_request(payload, "test-request")
        business = normalize_response(payload, request, response())["result"]
        product = Product(id="test-product", dataItems=[StructuredDataItem(data=business)])
        decoded = Product.model_validate_json(product.model_dump_json())
        self.assertEqual(decoded.dataItems[0].data, business)
        self.assertNotIn("status", decoded.dataItems[0].data)
