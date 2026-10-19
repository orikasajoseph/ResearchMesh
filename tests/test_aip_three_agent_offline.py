"""Three Partner official AIP flow with fixed QLoRA-shaped data and no network."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from acps_sdk.aip import AipRpcClient
import httpx

from aip_business_partner import create_local_business_app
from aip_lifecycle import sdk_sender
from aip_literature_partner import make_literature_executor
from aip_orchestrator import AipOrchestrator
from aip_reader_partner import make_reader_executor
from aip_repository_partner import make_repository_executor
from agents.repository.researchmesh_repository.agent import RepositoryAgent
from agents.repository.researchmesh_repository.enrichment import RepositoryEnricher
from agents.repository.tests.test_repository_agent import FakeStaticProvider
from literature_adapter import load_wrapper
from planner import Planner
from reader_adapter import to_reader_request
from resolver.local_aip import LocalAipResolver
from result_contract import validate_result


ROOT = Path(__file__).resolve().parents[1]
PDF = "https://arxiv.org/pdf/2305.14314"
REPO = "https://github.com/artidoro/qlora"
PAPER = {"paperId": "qlora-fixed", "title": "QLoRA: Efficient Finetuning of Quantized LLMs",
         "doi": "10.48550/arXiv.2305.14314", "url": "https://arxiv.org/abs/2305.14314",
         "pdfUrl": PDF, "repositoryUrls": [REPO]}


def plan(query):
    return {"schemaVersion": "0.2", "userQuery": query, "needsClarification": False,
            "clarificationQuestion": None, "tasks": [
        {"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
         "goal": "Find the QLoRA paper", "input": {"query": "QLoRA"},
         "constraints": {"maxResults": 1}, "outputSchema": "paper_records.v1",
         "requiredSkill": "literature.search", "dependsOn": []},
        {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
         "goal": "Read the found paper", "input": {"papers": []},
         "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1",
         "requiredSkill": "paper.analyze", "dependsOn": ["S1"]},
        {"schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
         "goal": "Relate paper evidence to static repository evidence",
         "input": {"papers": [], "paperNotes": [], "repositoryUrls": []},
         "constraints": {"maxRepositories": 1, "executeCode": False,
                         "allowRepositorySearch": False, "requirePaperNotes": True},
         "outputSchema": "code_records.v1", "requiredSkill": "repository.analyze",
         "dependsOn": ["S2"]}]}


def note(paper):
    return {"source": deepcopy(paper), "method": "Quantized base model with low-rank adapters.",
            "findings": "QLoRA describes 4-bit quantization and adapter training.",
            "limitations": "This offline fixture does not establish reproduction.",
            "analysisLevel": "fulltext", "evidence": [{
                "sourceId": paper["paperId"], "url": paper["pdfUrl"], "locator": "page 3, method",
                "supports": "The paper describes 4-bit quantization with low-rank adapters.",
                "text": "Fixed paper excerpt", "summary": "method", "score": 1.0}]}


class FixedPlannerModel:
    async def generate(self, instructions, user_query):
        return json.dumps(plan(user_query))


class FixedLLM:
    def __init__(self):
        self.bundles = []

    def generate_json(self, messages, *, schema=None):
        bundle = json.loads(messages[0]["content"].split("Evidence bundle:\n", 1)[1])
        self.bundles.append(bundle)
        return {"overview": None, "reproductionBarriers": [], "paperCodeRelations": [{
            "claim": "The paper's adapter method relates to the cited repository component.",
            "paperEvidenceIds": [bundle["paperNotes"][0]["evidence"][0]["evidenceId"]],
            "codeEvidenceIds": [bundle["evidence"][0]["evidenceId"]]}]}


class ThreeAgentAipTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, *, pdf=True, repository=True, s1_failure=False):
        query = "Find QLoRA, read the PDF, and inspect its code"
        planned = await Planner(FixedPlannerModel(), max_attempts=1).plan(query)
        paper = deepcopy(PAPER)
        if not pdf:
            paper["pdfUrl"] = None
        if not repository:
            paper["repositoryUrls"] = []
        wrapper = load_wrapper(ROOT / "agents/literature")
        provider_module = __import__(wrapper.__name__ + ".provider", fromlist=["ProviderSearchResult"])
        schema_module = __import__(wrapper.__name__ + ".schemas", fromlist=["PaperRecord"])
        seen, wire = {"literature": [], "reader": [], "repository": []}, {}

        class FixedProvider:
            async def search(self, request):
                seen["literature"].append(request.model_dump())
                if s1_failure:
                    raise provider_module.ProviderRequestError("offline provider failure", attempts=1)
                return provider_module.ProviderSearchResult(
                    [schema_module.PaperRecord(**paper, year=2023)], 1, 1, False)

        prepared = (SimpleNamespace(LiteratureAgent=wrapper.LiteratureAgent,
                    LiteratureSearchRequest=wrapper.LiteratureSearchRequest,
                    SemanticScholarProvider=lambda **kwargs: FixedProvider()), object())
        literature = make_literature_executor(ROOT / "agents/literature", prepared=prepared)
        expected = to_reader_request({key: value for key, value in
            {**planned["tasks"][1], "input": {"papers": [paper]}}.items()
            if key not in {"requiredSkill", "dependsOn"}})
        async def worker():
            seen["reader"].append(deepcopy(expected))
            raw = {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
                   "summary": "Fixed fulltext paper note", "warnings": [],
                   "items": [{"kind": "paper_note", "data": note(paper)}]}
            if not pdf:
                raw.update(summary="Full text unavailable", warnings=["FULL_TEXT_UNAVAILABLE"], items=[])
            return {"phase": "completed", "task": expected,
                    "outcome": {"result": raw, "error": None}}
        reader = make_reader_executor(worker, expected)
        llm = FixedLLM()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Fixed QLoRA repository snapshot\n", encoding="utf-8")
            class FixedRepository:
                def analyze(self, request):
                    seen["repository"].append(request.model_dump(mode="json"))
                    return RepositoryAgent(codewiki_provider=FakeStaticProvider(),
                        enricher=RepositoryEnricher(llm)).analyze_local(request, root, repository_url=REPO)
            apps = {
                "literature.search": create_local_business_app(execute=literature, partner_aic="s1-offline",
                    background_execution=True, mode="local-literature-live"),
                "paper.analyze": create_local_business_app(execute=reader, partner_aic="s2-offline",
                    background_execution=True, mode="local-reader-live"),
                "repository.analyze": create_local_business_app(
                    execute=make_repository_executor(FixedRepository()), partner_aic="s3-offline",
                    background_execution=True, mode="local-repository-live")}
            clients, mapping = [], {}
            try:
                for skill, app in apps.items():
                    wire[skill] = []
                    def traced_client_type(events):
                        class TracedClient(AipRpcClient):
                            async def _send_request(self, command):
                                response = await super()._send_request(command)
                                events.append((command.command.value, response.result.status.state.value,
                                               command.model_dump(mode="json", exclude_none=True)))
                                return response
                        return TracedClient
                    client = traced_client_type(wire[skill])("http://127.0.0.1/rpc", "three-agent-leader",
                        identity_binding_enabled=False, transport=httpx.ASGITransport(app=app))
                    clients.append(client)
                    mapping[skill] = ({"literature.search": "s1-offline", "paper.analyze": "s2-offline",
                                       "repository.analyze": "s3-offline"}[skill], sdk_sender(client), 5, .001)
                outcome = await AipOrchestrator(LocalAipResolver(mapping)).run_plan(planned)
            finally:
                for client in clients:
                    await client.close()
        return planned, outcome, seen, wire, llm

    async def test_complete_three_agent_chain(self):
        planned, outcome, seen, wire, llm = await self.run_case()
        self.assertEqual([task["dependsOn"] for task in planned["tasks"]], [[], ["S1"], ["S2"]])
        records = outcome["executionRecords"]
        self.assertEqual(outcome["executionWaves"], [["S1"], ["S2"], ["S3"]])
        self.assertEqual([r["succeeded"] for r in records], [True, True, True])
        self.assertEqual([r["aipState"] for r in records], ["completed"] * 3)
        self.assertEqual([r["taskType"] for r in outcome["results"]],
                         ["literature_search", "paper_read", "repository_analyze"])
        self.assertEqual(records[1]["boundInput"]["papers"][0], PAPER)
        self.assertEqual(records[2]["boundInput"]["repositoryUrls"], [REPO])
        self.assertEqual(records[2]["boundInput"]["paperNotes"][0]["source"], PAPER)
        self.assertEqual(len(seen["repository"]), 1)
        self.assertEqual(seen["repository"][0]["input"]["repositoryUrls"], [REPO])
        self.assertEqual(len(seen["repository"][0]["input"]["paperNotes"]), 1)
        self.assertEqual(len(llm.bundles), 1)
        self.assertEqual(len(llm.bundles[0]["paperNotes"]), 1)
        final = outcome["results"][2]["items"][0]["data"]
        self.assertTrue(final["evidence"])
        self.assertTrue(final["paperEvidence"])
        self.assertTrue(final["paperCodeRelations"])
        validate_result(outcome["results"][2], {key: value for key, value in
            {**planned["tasks"][2], "input": records[2]["boundInput"]}.items()
            if key not in {"requiredSkill", "dependsOn"}})
        for skill in ("literature.search", "paper.analyze", "repository.analyze"):
            events = wire[skill]
            self.assertEqual(events[0][0], "start")
            self.assertEqual(events[-1][:2], ("complete", "completed"))
            self.assertIn("awaiting-completion", [state for _, state, _ in events])
            self.assertEqual(set(events[0][2]["dataItems"][0]["data"]),
                {"schemaVersion", "subtaskId", "taskType", "goal", "input", "constraints", "outputSchema"})
        self.assertFalse(outcome["realResearchVerified"])

    async def test_missing_pdf_skips_repository_without_false_note(self):
        _, outcome, seen, _, _ = await self.run_case(pdf=False)
        records = outcome["executionRecords"]
        self.assertEqual([r["succeeded"] for r in records], [True, False, False])
        self.assertEqual(records[1]["error"]["code"], "PARTNER_FAILED")
        self.assertEqual(records[2]["error"]["code"], "DEPENDENCY_FAILED")
        self.assertEqual(len(outcome["results"]), 1)
        self.assertFalse(seen["repository"])

    async def test_missing_repository_url_stops_s3_before_partner(self):
        _, outcome, seen, wire, _ = await self.run_case(repository=False)
        records = outcome["executionRecords"]
        self.assertEqual([r["succeeded"] for r in records], [True, True, False])
        self.assertEqual(records[2]["error"]["code"], "INPUT_INSUFFICIENT")
        self.assertFalse(wire["repository.analyze"])
        self.assertFalse(seen["repository"])

    async def test_upstream_failure_skips_both_dependents(self):
        _, outcome, seen, wire, _ = await self.run_case(s1_failure=True)
        records = outcome["executionRecords"]
        self.assertEqual([r["succeeded"] for r in records], [False, False, False])
        self.assertEqual([r["error"]["code"] for r in records],
                         ["PARTNER_FAILED", "DEPENDENCY_FAILED", "DEPENDENCY_FAILED"])
        self.assertFalse(seen["reader"])
        self.assertFalse(seen["repository"])
        self.assertFalse(wire["paper.analyze"])
        self.assertFalse(wire["repository.analyze"])
        self.assertFalse(outcome["results"])


if __name__ == "__main__":
    unittest.main()
