"""Offline S2 paper_notes.v1 -> official AIP S3 binding and enrichment checks."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from acps_sdk.aip import AipRpcClient
import httpx

from aip_business_partner import create_local_business_app, validate_local_task
from aip_lifecycle import sdk_sender
from aip_orchestrator import AipOrchestrator
from aip_repository_partner import make_repository_executor, repository_request
from agents.repository.researchmesh_repository.agent import RepositoryAgent
from agents.repository.researchmesh_repository.enrichment import RepositoryEnricher
from input_binding import InputBindingError, bind_task_input
from resolver.local_aip import LocalAipResolver
from result_contract import validate_result
from run_aip_repository import repository_plan
from run_aip_s2_s3 import s2_s3_plan
from agents.repository.tests.test_repository_agent import FakeStaticProvider


REPO = "https://github.com/pypa/sampleproject"
PDF = "https://example.org/paper.pdf"


def paper():
    return {"paperId": "paper-1", "title": "Example paper", "doi": "10.1234/example",
            "url": "https://example.org/paper", "pdfUrl": PDF, "repositoryUrls": [REPO]}


def paper_note():
    return {"source": paper(), "method": "The paper describes a sample package method.",
            "findings": "The package exposes a documented entrypoint.",
            "limitations": "No executed reproduction was reported.",
            "analysisLevel": "fulltext", "evidence": [{
                "sourceId": "paper-1", "url": PDF, "locator": "page 2, method",
                "supports": "The paper describes the package method.",
                "text": "A sample package method is described here.",
                "summary": "Method context", "score": 1.0}]}


def s3_payload(notes=None):
    task = repository_plan([REPO])["tasks"][0]
    task["input"]["paperNotes"] = notes or []
    return {key: deepcopy(value) for key, value in task.items()
            if key not in {"requiredSkill", "dependsOn"}}


class RecordingLLM:
    def __init__(self, *, invalid_ids=False, claim="The paper's method relates to this code evidence."):
        self.calls = []
        self.invalid_ids = invalid_ids
        self.claim = claim

    def generate_json(self, messages, *, schema=None):
        prompt = messages[0]["content"]
        self.calls.append((prompt, schema))
        if "paperNotes" not in prompt:
            return {"overview": None, "reproductionBarriers": []}
        bundle = json.loads(prompt.split("Evidence bundle:\n", 1)[1])
        return {"overview": None, "reproductionBarriers": [],
                "paperCodeRelations": [{"claim": self.claim,
                    "paperEvidenceIds": ["P-missing" if self.invalid_ids else
                                         bundle["paperNotes"][0]["evidence"][0]["evidenceId"]],
                    "codeEvidenceIds": ["E-missing" if self.invalid_ids else
                                        bundle["evidence"][0]["evidenceId"]]}]}


class RepositoryPaperTests(unittest.TestCase):
    def test_live_runner_plan_uses_existing_skills_and_dependency(self):
        reader = {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
                  "goal": "Read one paper", "input": {"papers": [paper()]},
                  "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1"}
        plan = s2_s3_plan(reader, [REPO])
        self.assertEqual([task["requiredSkill"] for task in plan["tasks"]],
                         ["paper.analyze", "repository.analyze"])
        self.assertEqual(plan["tasks"][1]["dependsOn"], ["S2"])
        self.assertTrue(plan["tasks"][1]["constraints"]["requirePaperNotes"])

    def test_partner_projects_real_note_and_keeps_seven_fields(self):
        request = repository_request(s3_payload([paper_note()]))
        self.assertEqual(set(request.model_dump()), {"schemaVersion", "subtaskId", "taskType",
                         "goal", "input", "constraints", "outputSchema"})
        projected = request.input.paperNotes[0]
        self.assertEqual(projected.source.repositoryUrls, [REPO])
        self.assertEqual(projected.evidence[0].sourceId, "paper-1")
        self.assertTrue(projected.evidence[0].evidenceId.startswith("P-"))
        self.assertEqual(projected.evidence[0].evidenceId,
                         repository_request(s3_payload([paper_note()])).input.paperNotes[0].evidence[0].evidenceId)
        self.assertNotIn("summary", projected.evidence[0].model_dump())
        self.assertNotIn("score", projected.evidence[0].model_dump())

    def test_invalid_paper_note_rejected_before_backend(self):
        for change in (lambda n: n.pop("findings"),
                       lambda n: n["evidence"][0].update(sourceId="wrong"),
                       lambda n: n.update(isMock=True)):
            note = paper_note(); change(note)
            with self.subTest(note=note), self.assertRaises(ValueError):
                validate_local_task(s3_payload([note]))

    def test_missing_repository_url_stops_binding(self):
        task = repository_plan([])["tasks"][0]
        task["dependsOn"] = ["S2"]
        task["constraints"]["requirePaperNotes"] = True
        note = paper_note(); note["source"]["repositoryUrls"] = []
        result = {"items": [{"kind": "paper_note", "data": note}]}
        with self.assertRaises(InputBindingError):
            bind_task_input(task, {"S2": result})

    def test_real_note_reaches_agent_enrichment_and_relation_is_grounded(self):
        llm = RecordingLLM()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Sample package\n", encoding="utf-8")
            agent = RepositoryAgent(codewiki_provider=FakeStaticProvider(),
                                    enricher=RepositoryEnricher(llm))
            data = agent.analyze_local(repository_request(s3_payload([paper_note()])),
                                       root, repository_url=REPO).model_dump(mode="json")
        validate_result(data, s3_payload([paper_note()]))
        record = data["items"][0]["data"]
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(len(record["paperEvidence"]), 1)
        self.assertEqual(len(record["paperCodeRelations"]), 1)
        self.assertIn("paperNotes", llm.calls[0][0])
        self.assertEqual(record["paperCodeRelations"][0]["paperEvidenceIds"],
                         [record["paperEvidence"][0]["evidenceId"]])
        forged = deepcopy(data)
        forged["items"][0]["data"]["paperEvidence"][0]["evidenceId"] = "P-forged"
        with self.assertRaises(ValueError):
            validate_result(forged, s3_payload([paper_note()]))

    def test_unknown_paper_or_code_id_never_becomes_relation(self):
        llm = RecordingLLM(invalid_ids=True)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Sample package\n", encoding="utf-8")
            data = RepositoryAgent(codewiki_provider=FakeStaticProvider(),
                enricher=RepositoryEnricher(llm)).analyze_local(
                    repository_request(s3_payload([paper_note()])), root,
                    repository_url=REPO).model_dump(mode="json")
        self.assertEqual(data["items"][0]["data"]["paperCodeRelations"], [])
        self.assertIn("UNSUPPORTED_PAPER_CODE_CLAIM", data["warnings"])

    def test_unverified_execution_claim_is_rejected(self):
        llm = RecordingLLM(claim="We reproduced the paper by running the code.")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Sample package\n", encoding="utf-8")
            data = RepositoryAgent(codewiki_provider=FakeStaticProvider(),
                enricher=RepositoryEnricher(llm)).analyze_local(
                    repository_request(s3_payload([paper_note()])), root,
                    repository_url=REPO).model_dump(mode="json")
        self.assertEqual(data["items"][0]["data"]["paperCodeRelations"], [])
        self.assertIn("UNSUPPORTED_PAPER_CODE_CLAIM", data["warnings"])

    def test_no_note_preserves_repository_only_enrichment(self):
        llm = RecordingLLM()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Sample package\n", encoding="utf-8")
            data = RepositoryAgent(codewiki_provider=FakeStaticProvider(),
                enricher=RepositoryEnricher(llm)).analyze_local(
                    repository_request(s3_payload()), root,
                    repository_url=REPO).model_dump(mode="json")
        validate_result(data, s3_payload())
        self.assertEqual(data["items"][0]["data"]["paperCodeRelations"], [])
        self.assertEqual(data["items"][0]["data"]["paperEvidence"], [])
        self.assertNotIn("paperNotes", llm.calls[0][0])
        self.assertNotIn("paperCodeRelations", llm.calls[0][1]["properties"])


class S2ToS3AipTests(unittest.IsolatedAsyncioTestCase):
    async def test_reader_result_binds_url_and_note_through_partner(self):
        seen = []
        note = paper_note()
        llm = RecordingLLM()
        async def reader_execute(task):
            return {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
                    "summary": "Read one paper", "warnings": [],
                    "items": [{"kind": "paper_note", "data": note}]}
        class Agent:
            def analyze(self, request):
                seen.append(request.model_dump(mode="json"))
                return RepositoryAgent(codewiki_provider=FakeStaticProvider(),
                    enricher=RepositoryEnricher(llm)).analyze_local(
                        request, root, repository_url=REPO)
        s2 = {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
              "requiredSkill": "paper.analyze", "dependsOn": [], "goal": "Read paper",
              "input": {"papers": [paper()]}, "constraints": {"maxPapers": 1},
              "outputSchema": "paper_notes.v1"}
        s3 = repository_plan([])["tasks"][0]
        s3["dependsOn"] = ["S2"]
        s3["constraints"]["requirePaperNotes"] = True
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Sample package\n", encoding="utf-8")
            reader_app = create_local_business_app(execute=reader_execute,
                partner_aic="reader-test", mode="local-reader-live", background_execution=True)
            repo_app = create_local_business_app(execute=make_repository_executor(Agent()),
                partner_aic="repo-test", mode="local-repository-live", background_execution=True)
            reader_client = AipRpcClient("http://127.0.0.1/rpc", "local-reader-leader",
                identity_binding_enabled=False, transport=httpx.ASGITransport(app=reader_app))
            repo_client = AipRpcClient("http://127.0.0.1/rpc", "local-repo-leader",
                identity_binding_enabled=False, transport=httpx.ASGITransport(app=repo_app))
            try:
                resolver = LocalAipResolver({
                    "paper.analyze": ("reader-test", sdk_sender(reader_client), 5, .001),
                    "repository.analyze": ("repo-test", sdk_sender(repo_client), 5, .001)})
                outcome = await AipOrchestrator(resolver).run_plan({
                    "schemaVersion": "0.2", "userQuery": "Read and inspect code",
                    "needsClarification": False, "clarificationQuestion": None,
                    "tasks": [s2, s3]})
            finally:
                await reader_client.close(); await repo_client.close()
        self.assertEqual([record["succeeded"] for record in outcome["executionRecords"]], [True, True])
        self.assertEqual(outcome["executionRecords"][1]["boundInput"]["repositoryUrls"], [REPO])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["input"]["paperNotes"][0]["source"]["paperId"], "paper-1")
        self.assertEqual(set(seen[0]), {"schemaVersion", "subtaskId", "taskType", "goal",
                                      "input", "constraints", "outputSchema"})
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(len(outcome["results"][1]["items"][0]["data"]["paperCodeRelations"]), 1)


if __name__ == "__main__":
    unittest.main()
