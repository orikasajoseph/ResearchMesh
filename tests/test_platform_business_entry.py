"""Protocol-free business entry tests; real adapters/Wrapper, fake providers.

No platform credentials, external HTTP, actual PaperQA, PDFs, Git or models.
"""
import asyncio
import base64
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from aip_business_partner import LocalBackendFailure
from aip_literature_partner import make_literature_executor
from aip_reader_partner import make_reader_executor, create_reader_business_executor
from aip_repository_partner import make_repository_executor, create_repository_business_executor
from business_execution import execute_business_task, BusinessExecutionError
from common.models import BUSINESS_TASK_FIELDS, RESULT_FIELDS
from input_binding import bind_task_input
from reader_runtime import make_persistent_reader_executor
from reader_adapter import normalize_reader_result
from tests.test_reader_adapter import task as reader_task, result as reader_result
from tests.test_reader_repository_handoff import payload as repository_payload
from tests.test_repository_dual_mode import enhanced
from tests.test_aip_reader import journal
from tests.test_literature_openalex import TITLE, WORK


def business(task):
    return {k: deepcopy(task[k]) for k in BUSINESS_TASK_FIELDS}


class BusinessEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("network forbidden")))

    async def test_default_execution_denied_before_backend(self):
        backend = AsyncMock()
        for value in (False, "true", 1):
            with self.subTest(value=value), self.assertRaisesRegex(BusinessExecutionError, "NOT_AUTHORIZED"):
                await execute_business_task(business(reader_task()), skill="paper.analyze", backend=backend, allow_execution=value)
        backend.assert_not_awaited()

    async def test_internal_fields_status_wrong_skill_and_unsupported_limits_rejected(self):
        for change in ("internal", "status", "skill", "limits"):
            task = business(reader_task()); backend = AsyncMock()
            skill = "paper.analyze"
            if change == "internal": task["dependsOn"] = []
            elif change == "status": task["status"] = "working"
            elif change == "skill": skill = "repository.analyze"
            else: task["constraints"]["maxPapers"] = 2
            with self.subTest(change=change), self.assertRaises(BusinessExecutionError):
                await execute_business_task(task, skill=skill, backend=backend, allow_execution=True)
            backend.assert_not_awaited()

    async def test_six_field_result_no_business_status_and_no_input_mutation(self):
        task = business(reader_task()); before = deepcopy(task)
        async def backend(value):
            value["goal"] = "changed inside backend"
            return reader_result()
        result = await execute_business_task(task, skill="paper.analyze", backend=backend, allow_execution=True)
        self.assertEqual(set(result), RESULT_FIELDS)
        self.assertNotIn("status", result)
        self.assertEqual(task, before)

    async def test_wrong_source_or_mock_or_abstract_or_extra_result_field_rejected(self):
        for change in ("source", "mock", "abstract", "status"):
            result = reader_result()
            if change == "source": result["items"][0]["data"]["source"]["paperId"] = "wrong"
            elif change == "mock": result["items"][0]["data"]["isMock"] = True
            elif change == "abstract": result["items"][0]["data"]["analysisLevel"] = "abstract"
            else: result["status"] = "completed"
            with self.subTest(change=change), self.assertRaisesRegex(BusinessExecutionError, "INVALID_BUSINESS_RESULT"):
                await execute_business_task(business(reader_task()), skill="paper.analyze", backend=AsyncMock(return_value=result), allow_execution=True)

    async def test_timeout_cancels_once_no_retry_and_reports_uncertainty(self):
        calls, cancelled = [], []
        async def backend(task):
            calls.append(True)
            try: await asyncio.sleep(10)
            finally: cancelled.append(True)
        with self.assertRaises(BusinessExecutionError) as caught:
            await execute_business_task(business(reader_task()), skill="paper.analyze", backend=backend,
                                        allow_execution=True, timeout_seconds=.01)
        self.assertEqual(caught.exception.code, "BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED")
        self.assertTrue(caught.exception.remote_execution_uncertain)
        self.assertEqual(calls, [True]); self.assertEqual(cancelled, [True])

    async def test_cancel_propagates_not_fake_completion(self):
        started = asyncio.Event(); cancelled = []
        async def backend(task):
            started.set()
            try: await asyncio.sleep(10)
            finally: cancelled.append(True)
        running = asyncio.create_task(execute_business_task(business(reader_task()), skill="paper.analyze",
                                        backend=backend, allow_execution=True))
        await started.wait(); running.cancel()
        with self.assertRaises(asyncio.CancelledError): await running
        self.assertEqual(cancelled, [True])

    async def test_safe_error_code_preserved_not_raw_provider_exception(self):
        for error, expected in ((LocalBackendFailure("RATE_LIMITED"), "RATE_LIMITED"),
                                (RuntimeError("private-key-do-not-leak"), "BUSINESS_BACKEND_FAILED")):
            with self.subTest(expected=expected), self.assertRaises(BusinessExecutionError) as caught:
                await execute_business_task(business(reader_task()), skill="paper.analyze",
                                            backend=AsyncMock(side_effect=error), allow_execution=True)
            self.assertEqual(str(caught.exception), expected)
            self.assertNotIn("private-key", str(caught.exception))

    async def test_invalid_timeout_does_not_invoke_backend(self):
        for timeout in (0, True, float("nan"), 3601):
            backend = AsyncMock()
            with self.subTest(timeout=timeout), self.assertRaisesRegex(BusinessExecutionError, "INVALID_BUSINESS_TIMEOUT"):
                await execute_business_task(business(reader_task()), skill="paper.analyze", backend=backend,
                                            allow_execution=True, timeout_seconds=timeout)
            backend.assert_not_awaited()

    async def test_enhancement_uses_dual_evidence_and_failure_retains_static_result(self):
        task = repository_payload()
        for mode in ("ok", "timeout"):
            result, _, _ = enhanced(task, mode=mode)
            actual = await execute_business_task(task, skill="repository.analyze", backend=AsyncMock(return_value=result),
                                                allow_execution=True, repository_analysis_mode="enhanced")
            self.assertEqual(actual, result)
            self.assertTrue(actual["items"][0]["data"]["evidence"])
            if mode == "ok": self.assertTrue(actual["items"][0]["data"]["paperCodeRelations"])
            else: self.assertIn("LLM_ENRICHMENT_UNAVAILABLE", actual["warnings"])

    async def test_static_context_cannot_silently_accept_enhanced_backend(self):
        task = repository_payload(); result, _, _ = enhanced(task)
        with self.assertRaisesRegex(BusinessExecutionError, "INVALID_BUSINESS_RESULT"):
            await execute_business_task(task, skill="repository.analyze", backend=AsyncMock(return_value=result), allow_execution=True)

    async def test_false_enhancement_completion_without_dual_evidence_rejected(self):
        task = repository_payload(); result, _, _ = enhanced(task)
        result["items"][0]["data"]["paperCodeRelations"] = []
        with self.assertRaisesRegex(BusinessExecutionError, "INVALID_BUSINESS_RESULT"):
            await execute_business_task(task, skill="repository.analyze", backend=AsyncMock(return_value=result),
                                        allow_execution=True, repository_analysis_mode="enhanced")

    async def test_guarded_dynamic_repository_executor_does_not_reexecute_duplicate_task(self):
        task = repository_payload(); result, _, _ = enhanced(task)
        backend = AsyncMock(return_value={"scope": "ISOLATED_CODEWIKI_WITH_EXPLICIT_LLM_ENHANCEMENT",
                    "task": task, "phase": "completed", "error": None, "result": result})
        prepared = AsyncMock()
        execute = make_repository_executor(backend, analysis_mode="enhanced", prepare_task=prepared)
        self.assertEqual(await execute(task), result)
        self.assertEqual(await execute(task), result)
        changed = deepcopy(task); changed["goal"] = "another task"
        with self.assertRaisesRegex(LocalBackendFailure, "PREVIOUS_EXECUTION_UNCERTAIN"): await execute(changed)
        backend.assert_awaited_once(); prepared.assert_awaited_once_with(task)

    async def test_public_factories_construct_without_input_files_or_execution(self):
        with TemporaryDirectory() as directory, patch("subprocess.Popen", side_effect=AssertionError("no child")):
            root = Path(directory)
            for module, function, kwargs in (
                ("aip_reader_partner", create_reader_business_executor, {"reader_python": "fake.exe", "reader_root": root}),
                ("aip_repository_partner", create_repository_business_executor, {"repository_python": "fake.exe", "wrapper_root": root})):
                with patch(module + ".require_record_directory", return_value=root):
                    self.assertTrue(callable(function(record_dir=root, **kwargs)))
            self.assertEqual(list(root.iterdir()), [])

    async def test_persistent_reader_reuses_same_prepared_agent_for_different_tasks(self):
        model = SimpleNamespace(model_validate=lambda raw: deepcopy(raw))
        calls = []
        class Agent:
            async def run(self, task):
                calls.append(task)
                value = reader_result(); value["subtaskId"] = task["subtaskId"]
                value["items"][0]["data"]["source"] = deepcopy(task["input"]["papers"][0])
                value["items"][0]["data"]["evidence"][0].update(sourceId=task["input"]["papers"][0]["paperId"],
                                                                          url=task["input"]["papers"][0]["pdfUrl"])
                return SimpleNamespace(model_dump=lambda **_: value)
        prepare = AsyncMock()
        execute = make_persistent_reader_executor(prepared=(Agent(), model), prepare_task=prepare)
        first = business(reader_task()); second = deepcopy(first)
        second["subtaskId"] = "S2-next"; second["input"]["papers"][0]["paperId"] = "p-next"
        await execute(first); await execute(second)
        self.assertEqual(len(calls), 2); self.assertEqual(prepare.await_count, 2)

    async def test_openalex_resources_reader_repository_use_same_business_boundary(self):
        """Fake HTTP -> real Literature Wrapper -> real Reader Wrapper -> real Enricher."""
        from agents.literature.researchmesh_literature.agent import LiteratureAgent
        from agents.literature.researchmesh_literature.config import LiteratureAgentConfig
        from agents.literature.researchmesh_literature.provider import SemanticScholarProvider
        from agents.literature.researchmesh_literature.resource_resolver import ResourceResolver
        from agents.literature.researchmesh_literature.schemas import LiteratureSearchRequest
        from agents.paper_reader.researchmesh_paper_reader import PaperReaderAgent, PaperReadTask
        from agents.paper_reader.tests.test_researchmesh_paper_reader import FakeProvider
        repo = "https://github.com/pypa/sampleproject"
        def resource_handler(request):
            if request.url.path.startswith("/pdf/"): return httpx.Response(200, content=b"%PDF-offline-only")
            if request.url.path.startswith("/abs/"):
                return httpx.Response(200, text=f'<meta name="citation_title" content="{TITLE}">')
            if request.url.path == "/search/repositories":
                return httpx.Response(200, json={"items": [{"html_url": repo, "fork": False}]})
            if request.url.path.endswith("/readme"):
                return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(
                    (TITLE + " arXiv:2305.14314 paper implementation").encode()).decode()})
            return httpx.Response(404)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": [WORK]}))) as oa, \
                   httpx.AsyncClient(transport=httpx.MockTransport(resource_handler)) as resources:
            config = LiteratureAgentConfig(max_retries=0, openalex_fallback=True, resolve_resources=True)
            sdk = SimpleNamespace(search_paper=AsyncMock(side_effect=ConnectionRefusedError("429")))
            provider = SemanticScholarProvider(config=config, client=sdk, openalex_client=oa)
            wrapper = SimpleNamespace(SemanticScholarProvider=lambda **_: provider, LiteratureAgent=LiteratureAgent,
                                      LiteratureSearchRequest=LiteratureSearchRequest)
            s1 = {"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search", "goal": "offline find",
                  "input": {"query": TITLE}, "constraints": {"maxResults": 1}, "outputSchema": "paper_records.v1"}
            with patch("agents.literature.researchmesh_literature.provider.ResourceResolver",
                       side_effect=lambda **kw: ResourceResolver(client=resources, max_requests=kw["max_requests"])):
                r1 = await execute_business_task(s1, skill="literature.search", backend=make_literature_executor(
                    "unused", prepared=(wrapper, config)), allow_execution=True)
        s2 = deepcopy(reader_task()); s2["input"]["papers"] = []; s2["dependsOn"] = ["S1"]
        bound2 = business(bind_task_input(s2, {"S1": r1}))
        reader_backend = make_persistent_reader_executor(prepared=(PaperReaderAgent(FakeProvider()), PaperReadTask),
                                                          prepare_task=AsyncMock())
        r2 = await execute_business_task(bound2, skill="paper.analyze", backend=reader_backend, allow_execution=True)
        s3 = {**repository_payload(), "input": {"papers": [], "paperNotes": [], "repositoryUrls": []},
              "requiredSkill": "repository.analyze", "dependsOn": ["S2"]}
        bound3 = business(bind_task_input(s3, {"S2": r2}))
        self.assertEqual(bound3["input"]["repositoryUrls"], [repo])
        self.assertEqual(bound3["input"]["paperNotes"][0], r2["items"][0]["data"])
        r3, _, _ = enhanced(bound3)
        result = await execute_business_task(bound3, skill="repository.analyze", backend=AsyncMock(return_value=r3),
                                             allow_execution=True, repository_analysis_mode="enhanced")
        self.assertTrue(result["items"][0]["data"]["paperCodeRelations"])
        self.assertEqual(provider.resource_diagnostics()["openalexRequests"], 1)
        sdk.search_paper.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
