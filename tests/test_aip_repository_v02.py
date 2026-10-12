"""Offline tests for Repository v0.2 normalizer and official SDK local AIP RPC."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from aip.repository_v02_bridge import (
    RepositoryEvidenceError, _github_blob_url, business_task_from_planner,
    create_repository_local_app, invoke_repository_aip, normalize_repository_result,
)
from aip.wu_delivery.structured import ResearchClient
from agents.repository.researchmesh_repository import RepositoryAgent
from agents.repository.tests.test_repository_agent import FakeFetcher, FakeStaticProvider


def planner_task(**kwargs):
    task = {
        "schemaVersion": "0.2", "subtaskId": "repo-1",
        "taskType": "repository_analyze", "goal": "Inspect an explicit repository",
        "input": {"repositoryUrls": ["https://github.com/pypa/sampleproject.git"]},
        "constraints": {"maxRepositories": 1, "executeCode": False,
                        "allowRepositorySearch": False},
        "outputSchema": "code_records.v1", "requiredSkill": "repository.analyze",
        "dependsOn": [],
    }
    task.update(kwargs)
    return task


def native_result(*, subtask_id="repo-1", evidence=None, include_item=True):
    items = []
    if include_item:
        items = [{"kind": "repository_record", "data": {
            "repository": {"url": "https://github.com/pypa/sampleproject.git",
                           "commitHash": "a" * 40},
            "structure": [], "dependencies": [], "entrypoints": [], "runCommands": [],
            "reproductionBarriers": [], "overview": None,
            "evidence": evidence if evidence is not None else [],
            "diagnostics": {
                "provider": "codewiki-static", "codewikiVersion": "2.0.0",
                "codewikiCommit": "f0975ea19b17568ceb55cd5422206e1a1ae13c0b",
                "metrics": {"cloneMs": 0, "staticAnalysisMs": 0,
                            "llmEnrichmentMs": 0, "totalMs": 0},
                "counts": {"componentCount": 0, "dependencyEdgeCount": 0,
                           "artifactCount": 0, "evidenceCount": 0},
            },
        }}]
    return {"schemaVersion": "0.2", "subtaskId": subtask_id,
            "taskType": "repository_analyze", "summary": "Static data",
            "items": items, "warnings": []}


def sample_evidence(**kwargs):
    e = {"evidenceId": "ev-1", "sourceType": "code", "filePath": "src/main.py",
         "symbol": None, "startLine": 2, "endLine": 5,
         "text": "def main(): pass", "supports": ["Entry point declaration"]}
    e.update(kwargs)
    return e


class RepositoryBridgeUnitTests(unittest.TestCase):
    def test_only_seven_business_fields(self):
        request = business_task_from_planner(planner_task())
        self.assertEqual(len(request.model_dump()), 7)
        self.assertNotIn("allowRepositorySearch", request.model_dump()["constraints"])

    def test_wrong_skill_or_unresolved_dependency_rejected(self):
        with self.assertRaisesRegex(ValueError, "SKILL_MISMATCH"):
            business_task_from_planner(planner_task(requiredSkill="paper.analyze"))
        with self.assertRaisesRegex(ValueError, "DEPENDENCIES_NOT_EXECUTED"):
            business_task_from_planner(planner_task(dependsOn=["literature-1"]))

    def test_repository_search_true_rejected(self):
        task = planner_task()
        task["constraints"]["allowRepositorySearch"] = True
        with self.assertRaisesRegex(ValueError, "REPOSITORY_SEARCH_FORBIDDEN"):
            business_task_from_planner(task)

    def test_multiple_repositories_rejected(self):
        with self.assertRaisesRegex(ValueError, "MULTIPLE_REPOSITORIES_UNSUPPORTED"):
            business_task_from_planner(planner_task(input={"repositoryUrls": [
                "https://github.com/pypa/sampleproject.git",
                "https://github.com/example/second.git"]}))

    def test_fixture_is_mock_and_no_fabricated_evidence(self):
        task = business_task_from_planner(planner_task())
        result = normalize_repository_result(native_result(evidence=[sample_evidence()]),
                                             task, backend_mode="fixture")
        self.assertEqual(result["items"][0]["kind"], "repository")
        self.assertTrue(result["items"][0]["data"]["isMock"])
        self.assertEqual(result["items"][0]["data"]["evidence"], [])
        self.assertIn("FIXTURE_ONLY_NO_REAL_CODEWIKI_ANALYSIS", result["warnings"])

    def test_empty_result_not_research(self):
        task = business_task_from_planner(planner_task())
        result = normalize_repository_result(native_result(include_item=False),
                                             task, backend_mode="fixture")
        self.assertEqual(result["items"], [])

    def test_correlation_mismatch_rejected(self):
        task = business_task_from_planner(planner_task())
        with self.assertRaisesRegex(ValueError, "CORRELATION"):
            normalize_repository_result(native_result(subtask_id="wrong"), task)

    def test_real_evidence_requires_sources(self):
        task = business_task_from_planner(planner_task())
        with self.assertRaisesRegex(RepositoryEvidenceError, "REPOSITORY_EVIDENCE_UNAVAILABLE"):
            normalize_repository_result(native_result(), task, backend_mode="real")

    def test_real_normalizes_evidence_source(self):
        task = business_task_from_planner(planner_task())
        result = normalize_repository_result(native_result(evidence=[sample_evidence()]),
                                             task, backend_mode="real")
        data = result["items"][0]["data"]
        self.assertFalse(data["isMock"])
        self.assertEqual(data["license"], "UNKNOWN")
        self.assertEqual(data["evidence"][0]["supports"], "Entry point declaration")
        self.assertEqual(data["evidence"][0]["url"],
                         "https://github.com/pypa/sampleproject/blob/" + "a"*40 +
                         "/src/main.py#L2-L5")

    def test_reject_path_traversal_and_unsupported_host(self):
        with self.assertRaises(RepositoryEvidenceError):
            _github_blob_url("https://github.com/pypa/sampleproject.git", "a"*40,
                             "../.env", 1, 2)
        with self.assertRaises(RepositoryEvidenceError):
            _github_blob_url("https://example.org/project.git", "a"*40,
                             "main.py", 1, 2)


class RepositoryAipRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_offline_roundtrip_without_codewiki(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            (repo / "README.md").write_text("# offline\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text("[project]\nname='fixture'\n",
                                                     encoding="utf-8")
            agent = RepositoryAgent(fetcher=FakeFetcher(repo),
                                    codewiki_provider=FakeStaticProvider(),
                                    enrichment_enabled=False)
            app = create_repository_local_app(agent, backend_mode="fixture")
            client = ResearchClient(partner_url="http://127.0.0.1:8766/rpc",
                                    leader_id="local-leader", identity_binding_enabled=False,
                                    transport=httpx.ASGITransport(app=app))
            try:
                result = await invoke_repository_aip(
                    client, business_task_from_planner(planner_task()),
                    backend_mode="fixture")
            finally:
                await client.close()
        self.assertEqual(result.status, "completed", result.to_dict())
        self.assertTrue(result.protocol_completed, result.to_dict())
        self.assertTrue(result.business_validated, result.to_dict())
        self.assertFalse(result.research_succeeded, result.to_dict())
        self.assertEqual(result.result["items"][0]["kind"], "repository")


if __name__ == "__main__":
    unittest.main()
