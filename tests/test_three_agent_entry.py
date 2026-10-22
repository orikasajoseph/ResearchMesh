"""OFFLINE ONLY: real planner/binding/SDK/ASGI, substituted scientific backends.

No API key, HTTP sockets, model, Git clone, PDF fetch or embedding download.
Temporary synthetic checkpoints are NOT live scientific acceptance evidence.
"""
import asyncio
import base64
from copy import deepcopy
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from aip_business_partner import create_local_business_app
from aip_reader_inputs import prepare_reader_input
from common.models import BUSINESS_TASK_FIELDS
from planner import Planner
from repository_paper_context import attach_comparison
from repository_resource import resolve_resource, ResourceLookup
from run_aip_business import save_json
from run_aip_literature import invoke_business
from run_aip_repository import invoke_repository
import tests.test_aip_evidence_assembly as assembly_fixture
from tests.test_aip_reader import note_result, journal
from tests.test_repository_live_chain import synthetic_result
import run_aip_research as entry
import three_agent_pipeline as full

URL = "https://github.com/pypa/sampleproject"


def task3():
    return {"schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
            "goal": "结合全文笔记对照指定仓库；静态分析，不执行代码。",
            "input": {"papers": [], "paperNotes": [], "repositoryUrls": [URL]},
            "constraints": {"maxRepositories": 1, "executeCode": False,
                            "allowRepositorySearch": False, "requirePaperNotes": True},
            "outputSchema": "code_records.v1", "requiredSkill": "repository.analyze", "dependsOn": ["S2"]}


def fixture(directory):
    source, _, old_cache = assembly_fixture.AssemblyTests().files(directory)
    root = Path(directory) / "project"
    output = root / "outputs/aip-research-offline-test"
    output.mkdir(parents=True)
    cache = root / "outputs/pdf-cache"; cache.mkdir()
    for file in old_cache.glob("*.pdf"):
        shutil.copyfile(file, cache / file.name)
    plan = json.loads((source / "plan.json").read_text())
    plan["userQuery"] = "检索论文，阅读1篇，再结合笔记静态分析 " + URL + "，不执行代码。"
    plan["tasks"].append(task3())
    python = root / "fake-python.exe"; python.touch()
    package = root / "agents/repository/researchmesh_repository"; package.mkdir(parents=True)
    (package / "__init__.py").touch()
    args = SimpleNamespace(wrapper_root=root, reader_root=root, reader_python=python,
                           repository_root=package.parent, repository_python=python,
                           repository_analysis_mode="static", pdf_proof=None, llm_adapter="existing-async")
    state = {"scope": "LOCAL_AIP_SINGLE_ENTRY_V1", "query": plan["userQuery"], "stages": {}}
    entry.persist(output, state)
    return root, output, state, args, plan, source


class OfflineStages:
    def __init__(self, root, output, source, *, fail=None, missing_repository=False, enhancement="ok"):
        self.root, self.output, self.source = root, output, source
        self.calls, self.fail, self.missing_repository = [], fail, missing_repository
        self.enhancement = enhancement

    async def aip(self, payload, result, *, repository_plan=None):
        async def execute(task):
            if task != payload:
                raise AssertionError("wrong business payload")
            return deepcopy(result)
        app = create_local_business_app(execute=execute, partner_aic="offline-" + payload["subtaskId"],
                                       background_execution=True, mode={"literature_search": "local-literature-live",
                                           "paper_read": "local-reader-live", "repository_analyze": "local-repository-live"}[payload["taskType"]])
        transport = httpx.ASGITransport(app=app)
        if repository_plan:
            return await invoke_repository(repository_plan, "http://127.0.0.1/rpc", "offline-S3", transport=transport)
        return await invoke_business(payload, "http://127.0.0.1/rpc", "offline-" + payload["subtaskId"],
                                     transport=transport, poll_seconds=.001,
                                     expected_task_type=payload["taskType"])

    def __call__(self, command, log, timeout):
        name = Path(command[3]).stem
        self.calls.append(name)
        if name == self.fail:
            return 1
        log.write_text("OFFLINE SUBSTITUTED SCIENTIFIC BACKEND. No external calls.\n", encoding="utf-8")
        def arg(key): return Path(command[command.index(key) + 1])
        if name == "run_aip_literature":
            target = arg("--record-dir"); target.mkdir()
            plan = json.loads(arg("--plan").read_text())
            payload = {k: deepcopy(plan["tasks"][0][k]) for k in BUSINESS_TASK_FIELDS}
            backend = json.loads((self.source / "backend_outcome.json").read_text())
            result = backend["result"]
            outcome = asyncio.run(self.aip(payload, result))
            for file, value in (("plan", plan), ("payload", payload), ("backend_outcome", backend), ("outcome", outcome)):
                save_json(target / (file + ".json"), value)
        elif name == "aip_pdf_worker":
            save_json(arg("--output"), json.loads((self.source.parent / "proof.json").read_text()))
        elif name == "run_aip_reader":
            target = arg("--record-dir"); target.mkdir()
            prepared = prepare_reader_input(arg("--source"), arg("--pdf-proof"), cache_dir=self.root / "outputs/pdf-cache")
            payload = prepared["payload"]; result = note_result(payload)
            out = asyncio.run(self.aip(payload, result))
            backend = journal(payload); backend["pdf"] = prepared["pdf"]
            for file, value in (("reader_input", prepared), ("reader_journal", backend), ("outcome", out)):
                save_json(target / (file + ".json"), value)
        elif name == "repository_resource":
            request = json.loads(arg("--input").read_text())
            def resource_http(http_request):
                if request["explicitRepositoryUrls"]:
                    raise AssertionError("user-provided repository must not use HTTP")
                if http_request.url.host == "arxiv.org":
                    return httpx.Response(200, text=URL)
                text = request["paper"]["title"] + " " + request["paper"]["url"]
                return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(text.encode()).decode()})
            with httpx.Client(transport=httpx.MockTransport(resource_http)) as client:
                result = resolve_resource(request, client)
            if self.missing_repository:
                result.update(state="unavailable", repositoryUrls=[], reason="VERIFIED_REPOSITORY_NOT_FOUND")
            save_json(arg("--output"), result)
        elif name == "run_aip_repository":
            target = arg("--record-dir"); target.mkdir()
            plan = json.loads(arg("--plan").read_text())
            payload = {k: deepcopy(plan["tasks"][0][k]) for k in BUSINESS_TASK_FIELDS}
            result = synthetic_result()
            result = attach_comparison(result, payload, payload["input"]["paperNotes"])
            out = asyncio.run(self.aip(payload, result, repository_plan=plan))
            backend = {"scope": "ISOLATED_CODEWIKI_STATIC_NO_LLM", "phase": "completed", "task": payload,
                       "result": result, "error": None, "diagnostics": {"llmEnabled": False, "modelCalls": 0,
                       "pythonNetworkAttemptsBlocked": 0, "targetCodeExecuted": False,
                       "targetDependenciesInstalled": False, "paperContextCompared": True}}
            if command[command.index("--analysis-mode") + 1] == "enhanced":
                from tests.test_repository_dual_mode import enhanced, SCOPE
                result, observations, _ = enhanced(payload, mode=self.enhancement)
                out = asyncio.run(self.aip(payload, result, repository_plan=plan))
                backend.update(scope=SCOPE, result=result, diagnostics=observations)
            for file, value in (("plan", plan), ("repository_input", payload), ("backend_outcome", backend), ("outcome", out)):
                save_json(target / (file + ".json"), value)
            if self.enhancement != "ok":
                return 1
        else:
            raise AssertionError("unexpected stage " + name)
        return 0


class ThreeEntryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("external HTTP forbidden")))
        self.enterContext(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("external HTTP forbidden")))
        self.enterContext(patch.object(entry.subprocess, "Popen", side_effect=AssertionError("external process forbidden")))

    def run_saved(self, root, output, state, args, plan, runner):
        save_json(output / "plan.json", plan)
        with patch.object(entry, "ROOT", root):
            return entry.execute_pipeline(output, state, args, runner=runner)

    def test_new_question_real_planner_binding_official_sdk_all_three_and_report(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            calls = []
            class Model:
                async def generate(self, instructions, query):
                    calls.append(query); return json.dumps(plan)
            async def planner(query, **kwargs):
                return await Planner(Model()).plan(query), {"plannerMode": "offline-fake-model", "requestCount": 0}
            runner = OfflineStages(root, output, source)
            with patch.object(entry, "ROOT", root):
                result = entry.execute_pipeline(output, state, args, runner=runner, planner=planner)
                again = entry.execute_pipeline(output, state, args, runner=runner)
            self.assertEqual(calls, [state["query"]])
            self.assertEqual(runner.calls, ["run_aip_literature", "aip_pdf_worker", "run_aip_reader", "repository_resource", "run_aip_repository"])
            self.assertTrue(result["acceptance"]["allThreeBusinessReady"])
            self.assertEqual(len({r["aipTaskId"] for r in result["executionRecords"]}), 3)
            self.assertEqual([r["dependsOn"] for r in result["executionRecords"]], [[], ["S1"], ["S2"]])
            bound = result["executionRecords"][2]["payload"]
            self.assertEqual(set(bound), set(BUSINESS_TASK_FIELDS))
            self.assertEqual(bound["input"]["paperNotes"][0], result["executionRecords"][1]["result"]["items"][0]["data"])
            self.assertTrue(again["acceptance"]["allThreeBusinessReady"])
            report = (output / "report.md").read_text(encoding="utf-8")
            for text in ("Synthetic method", "Synthetic finding", "Synthetic limitation", "仓库分析", "未验证", "不等于"):
                self.assertIn(text, report)
            self.assertFalse(result["acceptance"]["platformDiscoveryUsed"])
            self.assertFalse(result["acceptance"]["repositoryEnhancementSucceeded"])

    def test_missing_resource_retains_validated_reader_and_never_runs_repository(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            runner = OfflineStages(root, output, source, missing_repository=True)
            with self.assertRaisesRegex(ValueError, "VERIFIED_REPOSITORY_URL_UNAVAILABLE"):
                self.run_saved(root, output, state, args, plan, runner)
            value = json.loads((output / "research_run.json").read_text())
            self.assertEqual(len(value["executionRecords"]), 2)
            self.assertFalse(value["acceptance"]["allThreeBusinessReady"])
            self.assertNotIn("run_aip_repository", runner.calls)
            self.assertIn("VERIFIED_REPOSITORY_URL_UNAVAILABLE", (output / "report.md").read_text(encoding="utf-8"))

    def test_automatic_resource_binding_without_repository_in_question(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            plan["tasks"][2]["input"]["repositoryUrls"] = []
            plan["userQuery"] = state["query"] = "检索论文，阅读1篇并静态分析有可核验地址的对应仓库。"
            # The unit fixture intentionally has a short title; replace it with a
            # real-length synthetic title consistently in search and PDF proof.
            title = "Synthetic research paper title"
            for name in ("outcome", "backend_outcome"):
                path = source / (name + ".json"); data = json.loads(path.read_text())
                result = data["execution"]["result"] if name == "outcome" else data["result"]
                result["items"][0]["data"]["title"] = title
                save_json(path, data)
            proof_file = source.parent / "proof.json"; proof = json.loads(proof_file.read_text())
            proof[0]["paperBefore"]["title"] = proof[0]["pageTitle"] = title
            save_json(proof_file, proof)
            result = self.run_saved(root, output, state, args, plan, OfflineStages(root, output, source))
            self.assertEqual(result["repositoryResource"]["selectionMode"], "verified_public_readme")
            self.assertEqual(result["executionRecords"][2]["payload"]["input"]["repositoryUrls"], [URL])

    def test_explicit_enhancement_uses_fake_provider_and_preserves_dual_evidence(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            args.repository_analysis_mode = "enhanced"
            result = self.run_saved(root, output, state, args, plan, OfflineStages(root, output, source))
            self.assertTrue(result["acceptance"]["repositoryEnhancementSucceeded"])
            self.assertEqual(result["executionRecords"][2]["acceptance"]["modelCalls"], 1)
            self.assertIn("论文与代码关联", (output / "report.md").read_text(encoding="utf-8"))

    def test_failed_optional_enrichment_retains_static_without_claiming_enhancement(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            args.repository_analysis_mode = "enhanced"
            runner = OfflineStages(root, output, source, enhancement="timeout")
            result = self.run_saved(root, output, state, args, plan, runner)
            self.assertTrue(result["acceptance"]["allThreeBusinessReady"])
            self.assertFalse(result["acceptance"]["repositoryEnhancementSucceeded"])
            self.assertEqual(state["state"], "completed_with_warnings")
            self.assertIn("增强未完成", (output / "report.md").read_text(encoding="utf-8"))
            again = self.run_saved(root, output, state, args, plan, runner)
            self.assertFalse(again["acceptance"]["repositoryEnhancementSucceeded"])
            self.assertEqual(runner.calls.count("run_aip_repository"), 1)

    def test_failed_reader_keeps_search_and_never_replays_paid_stage(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            runner = OfflineStages(root, output, source, fail="run_aip_reader")
            with self.assertRaisesRegex(ValueError, "STAGE_FAILED_READER"):
                self.run_saved(root, output, state, args, plan, runner)
            result = json.loads((output / "research_run.json").read_text())
            self.assertEqual([r["subtaskId"] for r in result["executionRecords"]], ["S1"])
            with self.assertRaisesRegex(ValueError, "STAGE_NOT_SAFE_TO_REPLAY_READER"):
                self.run_saved(root, output, state, args, plan, runner)
            self.assertEqual(runner.calls.count("run_aip_reader"), 1)

    def test_tampering_any_completed_artifact_blocks_before_commands(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            runner = OfflineStages(root, output, source)
            self.run_saved(root, output, state, args, plan, runner)
            save_json(output / "reader/outcome.json", {"tampered": True})
            count = len(runner.calls)
            with self.assertRaisesRegex(ValueError, "THREE_AGENT_CHECKPOINT_CHANGED"):
                self.run_saved(root, output, state, args, plan, runner)
            self.assertEqual(len(runner.calls), count)

    def test_resume_cannot_silently_enable_paid_enhancement(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            runner = OfflineStages(root, output, source)
            self.run_saved(root, output, state, args, plan, runner)
            args.repository_analysis_mode = "enhanced"
            with self.assertRaisesRegex(ValueError, "THREE_AGENT_CONFIG_CHANGED"):
                self.run_saved(root, output, state, args, plan, runner)
            self.assertEqual(len(runner.calls), 5)

    def test_unsupported_multi_paper_stops_before_scientific_commands(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            plan["tasks"][1]["constraints"]["maxPapers"] = 2
            runner = OfflineStages(root, output, source)
            with self.assertRaisesRegex(ValueError, "SINGLE_PAPER_REPOSITORY_DEPENDENCY_REQUIRED"):
                self.run_saved(root, output, state, args, plan, runner)
            self.assertEqual(runner.calls, [])

    def test_planner_invented_repository_rejected_before_commands(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            plan["tasks"][2]["input"]["repositoryUrls"] = ["https://github.com/invented/unknown"]
            runner = OfflineStages(root, output, source)
            with self.assertRaises(ValueError):
                self.run_saved(root, output, state, args, plan, runner)
            self.assertEqual(runner.calls, [])

    def test_clarification_saved_and_no_scientific_calls(self):
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            plan.update(needsClarification=True, clarificationQuestion="具体调研哪个方向？", tasks=[])
            value = self.run_saved(root, output, state, args, plan, OfflineStages(root, output, source))
            self.assertTrue(value["needsClarification"])
            self.assertIn("具体调研哪个方向", (output / "report.md").read_text(encoding="utf-8"))

    def test_default_cli_does_not_plan_or_start_any_process(self):
        with patch.object(entry, "execute_pipeline", side_effect=AssertionError("paid")):
            self.assertEqual(entry.main(["--query", "new question including repository"]), 0)

    def test_full_plan_free_validation_and_default_assembler_still_rejects_extra_task(self):
        from assemble_aip_evidence import assemble
        with TemporaryDirectory() as directory:
            root, output, state, args, plan, source = fixture(directory)
            save_json(output / "plan.json", plan)
            self.assertEqual(entry.main(["--plan", str(output / "plan.json")]), 0)
            save_json(source / "plan.json", plan)
            with self.assertRaisesRegex(ValueError, "ONLY_COMPLETE_TWO_TASK_PLAN_SUPPORTED"):
                assemble(source, Path(directory) / "reader", cache_dir=Path(directory) / "cache")


class ResourceTests(unittest.TestCase):
    def request(self):
        return {"paper": {"paperId": "arxiv:2106.09685", "title": "LoRA: Low-Rank Adaptation of Large Language Models",
                          "doi": None, "url": "https://arxiv.org/abs/2106.09685", "pdfUrl": None, "repositoryUrls": []},
                "searchData": {}, "explicitRepositoryUrls": []}

    def run_lookup(self, handler, request=None):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return resolve_resource(request or self.request(), client)

    def test_automatic_lookup_requires_real_shaped_both_sided_identity(self):
        requests = []
        def handler(request):
            requests.append(str(request.url))
            if request.url.host == "arxiv.org":
                return httpx.Response(200, text='paper code https://github.com/microsoft/LoRA')
            text = self.request()["paper"]["title"] + " https://arxiv.org/abs/2106.09685"
            return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(text.encode()).decode()})
        result = self.run_lookup(handler)
        self.assertEqual(result["repositoryUrls"], ["https://github.com/microsoft/LoRA"])
        self.assertTrue(result["verifiedRelation"])
        self.assertEqual(len(requests), 2)
        full.validate_resource(result, self.request())

    def test_missing_wrong_paper_unofficial_or_rate_limit_not_selected(self):
        for case in ("missing", "wrong-id", "title-only", "unofficial", "rate"):
            calls = []
            def handler(request):
                calls.append(request)
                if case == "rate": return httpx.Response(429)
                if request.url.host == "arxiv.org": return httpx.Response(200, text=URL)
                if request.url.path == "/search/repositories": return httpx.Response(200, json={"items": []})
                text = self.request()["paper"]["title"]
                if case == "missing": return httpx.Response(404)
                if case == "wrong-id": text += " 2106.096850"
                if case == "unofficial": text += " unofficial 2106.09685"
                return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(text.encode()).decode()})
            with self.subTest(case=case):
                result = self.run_lookup(handler)
                self.assertEqual(result["state"], "unavailable")
                self.assertEqual(result["repositoryUrls"], [])
                self.assertLessEqual(len(calls), 8)
                if case == "rate": self.assertEqual(len(calls), 1)

    def test_redirect_private_hosts_credentials_and_oversize_denied(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"}))) as client:
            lookup = ResourceLookup(client)
            self.assertIsNone(lookup.get("https://api.github.com/anything", purpose="test"))
            for url in ("http://127.0.0.1/secret", "https://user:secret@api.github.com/x", "https://evil.invalid/x"):
                with self.assertRaisesRegex(ValueError, "RESOURCE_HOST_NOT_ALLOWED"):
                    lookup.get(url, purpose="test")
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 262145))) as client:
            lookup = ResourceLookup(client)
            self.assertIsNone(lookup.get("https://arxiv.org/abs/2106.09685", purpose="test"))

    def test_explicit_repository_is_selection_not_verified_relation_and_no_network(self):
        request = self.request(); request["explicitRepositoryUrls"] = [URL]
        result = self.run_lookup(lambda r: self.fail("network must not occur"), request)
        self.assertFalse(result["verifiedRelation"])
        self.assertEqual(result["events"], [])
        self.assertEqual(result["selectionMode"], "user_provided")

    def test_request_budget_and_timeout_do_not_retry_or_call_model(self):
        seen = []
        def handler(request): seen.append(request); raise httpx.ReadTimeout("do-not-leak", request=request)
        result = self.run_lookup(handler)
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["modelCalls"], 0)
        self.assertNotIn("do-not-leak", json.dumps(result))
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as client:
            lookup = ResourceLookup(client, budget=2)
            for _ in range(5): lookup.get("https://arxiv.org/abs/2106.09685", purpose="test")
            self.assertEqual(len(lookup.events), 2)


if __name__ == "__main__":
    unittest.main()
