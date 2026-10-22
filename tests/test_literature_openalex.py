"""OpenAlex fallback tests: every OpenAlex response is served by MockTransport."""
import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import base64
import os
import ssl

import httpx

from agents.literature.researchmesh_literature.agent import LiteratureAgent
from agents.literature.researchmesh_literature.config import LiteratureAgentConfig
from agents.literature.researchmesh_literature.openalex import map_work
from agents.literature.researchmesh_literature.provider import SemanticScholarProvider
from agents.literature.researchmesh_literature.resource_resolver import ResourceResolver
from agents.literature.researchmesh_literature.schemas import LiteratureSearchRequest
from literature_adapter import normalize_response
from orchestrator import make_task_payload
from aip_business_partner import create_local_business_app
from aip_literature_partner import make_literature_executor
from run_aip_literature import invoke_literature
from run_literature import prepare_live_wrapper


TITLE = "QLoRA: Efficient Finetuning of Quantized LLMs"
WORK = {"id": "https://openalex.org/W12345", "doi": "https://doi.org/10.48550/arXiv.2305.14314",
        "ids": {"doi": "https://doi.org/10.48550/arXiv.2305.14314"},
        "display_name": TITLE, "publication_year": 2023, "cited_by_count": 25,
        "authorships": [{"author": {"id": "https://openalex.org/A1", "display_name": "Tim Dettmers"}}],
        "best_oa_location": {"is_oa": True, "pdf_url": "https://arxiv.org/pdf/2305.14314"},
        "locations": [{"landing_page_url": "https://arxiv.org/abs/2305.14314"}]}


class OpenAlexTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_ended_year_range_is_not_mistaken_for_single_year(self):
        for year, expected in (("2022:", 1), (":2023", 1), ("2023", 1), ("2024:", 0)):
            sdk = SimpleNamespace(search_paper=AsyncMock())
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, json={"results": [WORK]}))) as oa:
                provider = SemanticScholarProvider(config=LiteratureAgentConfig(search_provider="openalex", max_retries=0),
                                                    client=sdk, openalex_client=oa)
                request = LiteratureSearchRequest(requestId="range-" + year, query=TITLE, limit=1,
                                                  filters={"year": year})
                with self.subTest(year=year):
                    self.assertEqual(len((await provider.search(request)).papers), expected)
                sdk.search_paper.assert_not_awaited()

    async def test_configuration_is_opt_in_and_reads_local_settings_without_network(self):
        wrapper = SimpleNamespace(LiteratureAgentConfig=LiteratureAgentConfig)
        with patch("run_literature.load_wrapper", return_value=wrapper), \
             patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            os.environ["SEMANTIC_SCHOLAR_API_KEY"] = "offline-key"
            _, config = prepare_live_wrapper("unused")
            self.assertFalse(config.openalex_fallback)
            self.assertFalse(config.resolve_resources)
        settings = {"LITERATURE_SEARCH_PROVIDER": "openalex", "LITERATURE_OPENALEX_FALLBACK": "1",
                    "LITERATURE_RESOLVE_RESOURCES": "1", "LITERATURE_RESOURCE_MAX_REQUESTS": "8"}
        with patch("run_literature.load_wrapper", return_value=wrapper), \
             patch("dotenv.dotenv_values", return_value=settings), patch.dict(os.environ, {}, clear=True):
            _, config = prepare_live_wrapper("unused")
            self.assertTrue(config.openalex_fallback); self.assertTrue(config.resolve_resources)
            self.assertEqual(config.search_provider, "openalex"); self.assertEqual(config.resource_max_requests, 8)
        from literature_adapter import AdapterError
        with patch("run_literature.load_wrapper", return_value=wrapper), \
             patch("dotenv.dotenv_values", return_value={**settings, "LITERATURE_RESOURCE_MAX_REQUESTS": "999"}), \
             patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(AdapterError, "INVALID_LITERATURE_CONFIGURATION"):
                prepare_live_wrapper("unused")

    async def test_direct_option_only_changes_openalex_client(self):
        requests = []

        def handler(request):
            requests.append(request.url.path)
            return httpx.Response(200, json={"results": [WORK]})

        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        sdk = SimpleNamespace(search_paper=AsyncMock(side_effect=ConnectionRefusedError("HTTP 429")))
        config = LiteratureAgentConfig(max_retries=0, openalex_fallback=True,
                                       openalex_trust_env=False)

        def client_factory(*args, **kwargs):
            return original_client(*args, transport=transport, **kwargs)

        with patch("agents.literature.researchmesh_literature.openalex.httpx.AsyncClient",
                   side_effect=client_factory) as constructor:
            provider = SemanticScholarProvider(config=config, client=sdk)
            result = await LiteratureAgent(provider).literature_search(
                LiteratureSearchRequest(requestId="direct", query=TITLE, limit=1))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.provider, "openalex")
        self.assertEqual(requests, ["/works"])
        self.assertEqual(provider.resource_diagnostics()["openalexTrustEnv"], False)
        self.assertEqual(constructor.call_args.kwargs["trust_env"], False)
        sdk.search_paper.assert_awaited_once()

        healthy_sdk = SimpleNamespace(search_paper=AsyncMock(return_value=SimpleNamespace(
            items=[SimpleNamespace(raw_data={"paperId": "s1", "title": TITLE,
                                             "url": "https://www.semanticscholar.org/paper/s1"})],
            total=1)))
        with patch("agents.literature.researchmesh_literature.openalex.httpx.AsyncClient") as constructor:
            healthy = await LiteratureAgent(SemanticScholarProvider(
                config=config, client=healthy_sdk)).literature_search(
                    LiteratureSearchRequest(requestId="semantic", query=TITLE, limit=1))
        self.assertEqual(healthy.provider, "semantic-scholar")
        constructor.assert_not_called()
        healthy_sdk.search_paper.assert_awaited_once()

    async def test_live_entry_reads_direct_option_without_changing_default(self):
        wrapper = SimpleNamespace(LiteratureAgentConfig=LiteratureAgentConfig)
        with patch("run_literature.load_wrapper", return_value=wrapper), patch.dict(os.environ, {
                "SEMANTIC_SCHOLAR_API_KEY": "offline-key",
                "LITERATURE_OPENALEX_TRUST_ENV": "0"}):
            _, config = prepare_live_wrapper("unused")
        self.assertFalse(config.openalex_trust_env)
        with patch("run_literature.load_wrapper", return_value=wrapper), \
                patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {
                "SEMANTIC_SCHOLAR_API_KEY": "offline-key"}):
            os.environ.pop("LITERATURE_OPENALEX_TRUST_ENV", None)
            _, default_config = prepare_live_wrapper("unused")
        self.assertTrue(default_config.openalex_trust_env)
        with patch("run_literature.load_wrapper", return_value=wrapper), \
                patch("dotenv.dotenv_values", return_value={"LITERATURE_OPENALEX_TRUST_ENV": "0"}), \
                patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "offline-key"}):
            os.environ.pop("LITERATURE_OPENALEX_TRUST_ENV", None)
            _, file_config = prepare_live_wrapper("unused")
        self.assertFalse(file_config.openalex_trust_env)

    async def test_connect_error_reports_tls_eof_without_exception_text(self):
        def handler(request):
            try:
                raise ssl.SSLEOFError(8, "private proxy detail")
            except ssl.SSLEOFError as cause:
                raise httpx.ConnectError("private proxy detail", request=request) from cause

        sdk = SimpleNamespace(search_paper=AsyncMock(side_effect=ConnectionRefusedError("HTTP 429")))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as oa:
            provider = SemanticScholarProvider(config=LiteratureAgentConfig(
                max_retries=0, openalex_fallback=True), client=sdk, openalex_client=oa)
            result = await LiteratureAgent(provider).literature_search(
                LiteratureSearchRequest(requestId="tls-eof", query=TITLE, limit=1))
        diagnostics = provider.resource_diagnostics()
        self.assertEqual(result.status, "failure")
        self.assertEqual(result.error.code, "PROVIDER_UNAVAILABLE")
        self.assertEqual(diagnostics["openalexRequests"], 1)
        self.assertEqual(diagnostics["openalexTransportError"], "ConnectError")
        self.assertEqual(diagnostics["openalexTransportCategory"], "TLS_EOF")
        self.assertNotIn("private proxy detail", str(diagnostics))
        sdk.search_paper.assert_awaited_once()

    async def test_partner_deadline_cancels_backend_with_safe_diagnostic(self):
        task = make_task_payload({"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
                                  "goal": "find QLoRA", "input": {"query": TITLE}, "constraints": {"maxResults": 1},
                                  "outputSchema": "paper_records.v1", "requiredSkill": "literature.search", "dependsOn": []})
        saved, canceled = [], []
        async def slow_backend(*args, **kwargs):
            try:
                await asyncio.sleep(.2)
            except asyncio.CancelledError:
                canceled.append(True)
                raise
        with patch("aip_literature_partner.execute_live_task", side_effect=slow_backend):
            executor = make_literature_executor("unused", on_outcome=saved.append)
            app = create_local_business_app(execute=executor, partner_aic="deadline-offline",
                                            backend_timeout_seconds=.03,
                                            background_execution=True, mode="local-literature-live")
            outcome = await invoke_literature(task, "http://127.0.0.1/rpc", "deadline-offline",
                                              transport=httpx.ASGITransport(app=app), poll_seconds=.005)
        self.assertEqual(outcome["error"]["code"], "PARTNER_FAILED")
        self.assertEqual(canceled, [True])
        self.assertEqual(saved[0]["error"]["code"], "LITERATURE_BACKEND_CANCELED")

    async def test_unexpected_backend_error_keeps_safe_type_only(self):
        task = make_task_payload({"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
                                  "goal": "find QLoRA", "input": {"query": TITLE}, "constraints": {"maxResults": 1},
                                  "outputSchema": "paper_records.v1", "requiredSkill": "literature.search", "dependsOn": []})
        saved = []
        with patch("aip_literature_partner.execute_live_task",
                   side_effect=RuntimeError("secret-bearing-provider-detail")):
            executor = make_literature_executor("unused", on_outcome=saved.append)
            app = create_local_business_app(execute=executor, partner_aic="error-offline",
                                            background_execution=True, mode="local-literature-live")
            outcome = await invoke_literature(task, "http://127.0.0.1/rpc", "error-offline",
                                              transport=httpx.ASGITransport(app=app), poll_seconds=.005)
        self.assertEqual(outcome["error"]["code"], "PARTNER_FAILED")
        self.assertEqual(saved[0]["error"]["code"], "LITERATURE_RUNTIME_ERROR")
        self.assertEqual(saved[0]["diagnostics"]["exceptionType"], "RuntimeError")
        self.assertNotIn("secret-bearing", str(saved))

    async def run_search(self, ss_side_effect=None, *, oa_status=200, work=WORK,
                         forced=False, resolve=False):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(oa_status, json={"results": [work], "meta": {"count": 1}})

        sdk = SimpleNamespace(search_paper=AsyncMock())
        if ss_side_effect:
            sdk.search_paper.side_effect = ss_side_effect
        else:
            sdk.search_paper.return_value = SimpleNamespace(items=[SimpleNamespace(raw_data={
                "paperId": "s1", "title": TITLE, "externalIds": {"ArXiv": "2305.14314"},
                "url": "https://www.semanticscholar.org/paper/s1"})], total=1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as oa:
            provider = SemanticScholarProvider(config=LiteratureAgentConfig(
                max_retries=0, rate_limit_per_second=1000, openalex_fallback=True,
                search_provider="openalex" if forced else "semantic-scholar",
                resolve_resources=resolve), client=sdk, openalex_client=oa)
            request = LiteratureSearchRequest(requestId="x", query=TITLE, limit=1)
            result = await LiteratureAgent(provider).literature_search(request)
            return result, provider, sdk, calls, request

    async def test_semantic_scholar_200_does_not_call_openalex(self):
        result, provider, sdk, calls, _ = await self.run_search()
        self.assertEqual(result.provider, "semantic-scholar")
        self.assertEqual(calls, [])
        sdk.search_paper.assert_awaited_once()
        self.assertEqual(provider.resource_diagnostics()["openalexRequests"], 0)

    async def test_429_falls_back_once_and_remains_contract_compatible(self):
        result, provider, sdk, calls, request = await self.run_search(
            ConnectionRefusedError("HTTP 429"))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.provider, "openalex")
        self.assertEqual(len(calls), 1)
        self.assertEqual(provider.resource_diagnostics()["fallbackReason"], "SEMANTIC_SCHOLAR_429")
        self.assertEqual(provider.resource_diagnostics()["semanticScholarRequests"], 1)
        self.assertEqual(result.papers[0].doi, "10.48550/arXiv.2305.14314")
        self.assertEqual(result.papers[0].pdfUrl, "https://arxiv.org/pdf/2305.14314")
        task = make_task_payload({"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
                                  "goal": "find QLoRA", "input": {"query": TITLE}, "constraints": {"maxResults": 1},
                                  "outputSchema": "paper_records.v1", "requiredSkill": "literature.search", "dependsOn": []})
        normalized = normalize_response(task, {"schemaVersion": "literature_search.v0.1",
            "requestId": "x", "query": TITLE, "filters": {"year": None}, "limit": 1},
            result.model_dump(mode="json"))
        self.assertIsNone(normalized["error"])
        self.assertEqual(normalized["result"]["items"][0]["data"]["provider"], "openalex")
        self.assertEqual(normalized["result"]["items"][0]["data"]["repositoryUrls"], [])

    async def test_429_then_openalex_failure_is_explicit(self):
        for status in (429, 503):
            result, provider, _, calls, _ = await self.run_search(
                ConnectionRefusedError("429"), oa_status=status)
            self.assertEqual(result.status, "failure")
            self.assertEqual(result.error.code, "PROVIDER_UNAVAILABLE")
            self.assertEqual(len(calls), 1)
            self.assertEqual(provider.resource_diagnostics()["fallbackError"], f"OPENALEX_HTTP_{status}")

    async def test_forced_openalex_never_calls_semantic_scholar(self):
        result, _, sdk, calls, _ = await self.run_search(forced=True)
        self.assertEqual(result.provider, "openalex")
        self.assertEqual(len(calls), 1)
        sdk.search_paper.assert_not_awaited()

    async def test_identifier_mismatch_and_metadata_gaps(self):
        self.assertIsNone(map_work(WORK, "arXiv:9999.99999"))
        self.assertIsNone(map_work({**WORK, "display_name": "Unrelated paper"}, TITLE))
        sparse = {**WORK, "authorships": [], "best_oa_location": None,
                  "abstract_inverted_index": None, "locations": []}
        mapped = map_work(sparse, TITLE)
        self.assertIsNotNone(mapped)
        self.assertIsNone(mapped[0].pdfUrl)
        self.assertEqual(mapped[0].authors, [])
        self.assertEqual(mapped[1]["resourceProvider"], "openalex")

    async def test_fallback_product_crosses_existing_aip_partner(self):
        sdk = SimpleNamespace(search_paper=AsyncMock(side_effect=ConnectionRefusedError("429")))
        calls = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, json={"results": [WORK], "meta": {"count": 1}})

        def resources(request):
            if request.url.path == "/search/repositories":
                return httpx.Response(200, json={"items": [{"html_url": "https://github.com/artidoro/qlora", "fork": False}]})
            if request.url.path == "/repos/artidoro/qlora/readme":
                body = f"# {TITLE}\nPaper arXiv:2305.14314 implementation"
                return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(body.encode()).decode()})
            if request.url.path == "/abs/2305.14314":
                return httpx.Response(200, content=f'<meta name="citation_title" content="{TITLE}">'.encode())
            if request.url.path == "/pdf/2305.14314":
                return httpx.Response(200, content=b"%PDF-1.7 offline")
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as oa, \
                   httpx.AsyncClient(transport=httpx.MockTransport(resources)) as resource_client:
            config = LiteratureAgentConfig(max_retries=0, openalex_fallback=True,
                                           resolve_resources=True)
            provider = SemanticScholarProvider(config=config, client=sdk, openalex_client=oa)
            wrapper = SimpleNamespace(SemanticScholarProvider=lambda **_: provider,
                                      LiteratureAgent=LiteratureAgent,
                                      LiteratureSearchRequest=LiteratureSearchRequest)
            saved = []
            executor = make_literature_executor("unused", prepared=(wrapper, config),
                                                on_outcome=saved.append)
            app = create_local_business_app(execute=executor, partner_aic="openalex-offline",
                                            background_execution=True, mode="local-literature-live")
            task = make_task_payload({"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
                                      "goal": "find QLoRA", "input": {"query": TITLE}, "constraints": {"maxResults": 1},
                                      "outputSchema": "paper_records.v1", "requiredSkill": "literature.search", "dependsOn": []})
            with patch("agents.literature.researchmesh_literature.provider.ResourceResolver",
                       side_effect=lambda **kwargs: ResourceResolver(client=resource_client,
                                                                       max_requests=kwargs["max_requests"])):
                outcome = await invoke_literature(task, "http://127.0.0.1/rpc", "openalex-offline",
                                                  transport=httpx.ASGITransport(app=app), poll_seconds=0.001)
        self.assertIsNone(outcome["error"])
        self.assertEqual(outcome["execution"]["state"], "completed")
        self.assertEqual(outcome["execution"]["result"]["items"][0]["data"]["provider"], "openalex")
        self.assertEqual(outcome["execution"]["result"]["items"][0]["data"]["pdfUrl"],
                         "https://arxiv.org/pdf/2305.14314")
        self.assertEqual(outcome["execution"]["result"]["items"][0]["data"]["repositoryUrls"],
                         ["https://github.com/artidoro/qlora"])
        self.assertEqual(saved[0]["diagnostics"]["openalexRequests"], 1)
        self.assertEqual(len(calls), 1)
        sdk.search_paper.assert_awaited_once()

    async def test_openalex_metadata_reuses_resource_resolver(self):
        oa_calls, resource_calls = [], []

        def oa_handler(request):
            oa_calls.append(str(request.url))
            return httpx.Response(200, json={"results": [WORK]})

        def resource_handler(request):
            resource_calls.append(str(request.url))
            if request.url.path == "/pdf/2305.14314":
                return httpx.Response(200, content=b"%PDF-1.7 offline")
            if request.url.path == "/abs/2305.14314":
                return httpx.Response(200, content=f'<meta name="citation_title" content="{TITLE}">'.encode())
            if request.url.path == "/search/repositories":
                return httpx.Response(200, json={"items": [{"html_url": "https://github.com/artidoro/qlora", "fork": False}]})
            if request.url.path == "/repos/artidoro/qlora/readme":
                body = f"# {TITLE}\nPaper arXiv:2305.14314 implementation"
                return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(body.encode()).decode()})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(oa_handler)) as oa, \
                   httpx.AsyncClient(transport=httpx.MockTransport(resource_handler)) as resources:
            with patch("agents.literature.researchmesh_literature.provider.ResourceResolver",
                       side_effect=lambda **kwargs: ResourceResolver(client=resources,
                                                                       max_requests=kwargs["max_requests"])):
                sdk = SimpleNamespace(search_paper=AsyncMock(side_effect=ConnectionRefusedError("HTTP 429")))
                provider = SemanticScholarProvider(config=LiteratureAgentConfig(
                    openalex_fallback=True, resolve_resources=True, max_retries=0),
                    client=sdk, openalex_client=oa)
                request = LiteratureSearchRequest(requestId="x", query=TITLE, limit=1)
                result = await provider.search(request)
                cached = await provider.search(request)
        report = provider.resource_diagnostics()["resourceResolution"]["papers"][0]
        self.assertEqual(report["pdf"]["source"], "openalex.best_oa_location")
        self.assertEqual(result.papers[0].repositoryUrls[-1], "https://github.com/artidoro/qlora")
        self.assertTrue(cached.cache_hit)
        self.assertEqual(len(oa_calls), 1)
        self.assertGreaterEqual(len(resource_calls), 3)
        sdk.search_paper.assert_awaited_once()
        self.assertEqual(provider.resource_diagnostics()["fallbackReason"], "SEMANTIC_SCHOLAR_429")


if __name__ == "__main__":
    unittest.main()
