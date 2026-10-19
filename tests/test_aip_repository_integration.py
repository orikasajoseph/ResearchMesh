"""Offline S3 wiring tests: official SDK, ASGI transport, no GitHub or DeepSeek."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest

from acps_sdk.aip import AipRpcClient, StructuredDataItem, TaskCommand, TaskCommandType
from datetime import datetime, timezone
from uuid import uuid4
import httpx

from aip_business_partner import create_local_business_app, validate_local_task
from aip_lifecycle import sdk_sender
from aip_orchestrator import AipOrchestrator
from aip_repository_partner import make_repository_executor, repository_request
from resolver.base import NoMatchingAgentError
from resolver.local_aip import LocalAipResolver
from result_contract import validate_result
from run_aip_repository import repository_plan
from run_aip_literature import invoke_business


def repository_result():
    return {
        "schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
        "summary": "Offline static fixture", "warnings": [],
        "items": [{"kind": "repository_record", "data": {
            "repository": {"url": "https://github.com/pypa/sampleproject", "commitHash": "abc123"},
            "structure": [{"filePath": "main.py", "components": [{
                "componentId": "main.py::main", "name": "main", "componentType": "function",
                "symbol": "main", "startLine": 1, "endLine": 2, "evidenceId": "E1"}]}],
            "dependencies": [{"source": "main.py::main", "target": "service.py::run",
                              "evidenceIds": ["E1", "E2"]}],
            "entrypoints": [{"name": "sample", "entrypointType": "pyproject-script",
                             "target": "main:main", "source": "pyproject.toml", "evidenceIds": ["E3"]}],
            "runCommands": [{"command": "sample", "source": "pyproject.toml",
                             "type": "declared", "evidenceIds": ["E3"]}],
            "reproductionBarriers": [], "overview": None,
            "evidence": [
                {"evidenceId": "E1", "sourceType": "code_component", "filePath": "main.py",
                 "symbol": "main", "startLine": 1, "endLine": 2, "text": "def main(): pass", "supports": ["structure"]},
                {"evidenceId": "E2", "sourceType": "code_component", "filePath": "service.py",
                 "symbol": "run", "startLine": 1, "endLine": 2, "text": "def run(): pass", "supports": ["dependency"]},
                {"evidenceId": "E3", "sourceType": "artifact", "filePath": "pyproject.toml",
                 "symbol": "sample", "startLine": 1, "endLine": 1, "text": "sample=main:main", "supports": ["entrypoints"]},
            ],
            "diagnostics": {"provider": "codewiki-static", "codewikiVersion": "2.0.0",
                            "codewikiCommit": "f0975ea19b17568ceb55cd5422206e1a1ae13c0b",
                            "metrics": {"cloneMs": 1, "staticAnalysisMs": 2,
                                        "llmEnrichmentMs": 3, "totalMs": 6},
                            "counts": {"componentCount": 2, "dependencyEdgeCount": 1,
                                       "artifactCount": 1, "evidenceCount": 3}},
        }}],
    }


def payload():
    task = repository_plan(["https://github.com/pypa/sampleproject"])["tasks"][0]
    return {key: deepcopy(value) for key, value in task.items()
            if key not in {"requiredSkill", "dependsOn"}}


class RepositoryValidationTests(unittest.TestCase):
    def test_repository_task_and_output_schema_accepted(self):
        self.assertEqual(validate_local_task(payload())["taskType"], "repository_analyze")

    def test_unknown_task_still_rejected(self):
        value = payload(); value["taskType"] = "unknown"
        with self.assertRaises(ValueError): validate_local_task(value)

    def test_wrong_output_schema_rejected(self):
        value = payload(); value["outputSchema"] = "paper_records.v1"
        with self.assertRaises(ValueError): validate_local_task(value)

    def test_leader_fields_rejected(self):
        for key, value in (("requiredSkill", "repository.analyze"), ("dependsOn", [])):
            changed = payload(); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_local_task(changed)

    def test_adapter_preserves_strict_repository_schema(self):
        value = payload(); value["input"].update(papers=[], paperNotes=[])
        request = repository_request(value)
        self.assertEqual(set(request.model_dump()), set(payload()))
        self.assertEqual(set(request.input.model_dump()), {"repositoryUrls", "paperNotes"})
        self.assertEqual(request.input.paperNotes, [])
        self.assertEqual(set(request.constraints.model_dump()), {"executeCode", "maxRepositories"})

    def test_repository_result_accepted_and_hallucinated_evidence_rejected(self):
        value = repository_result()
        validate_result(value, payload())
        value["items"][0]["data"]["dependencies"][0]["evidenceIds"] = ["E404"]
        with self.assertRaises(ValueError): validate_result(value, payload())

    def test_repository_result_must_match_requested_url(self):
        value = repository_result()
        value["items"][0]["data"]["repository"]["url"] = "https://github.com/other/repo"
        with self.assertRaises(ValueError): validate_result(value, payload())


class RepositoryAipTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_advertises_only_repository_skill(self):
        app = create_local_business_app(execute=lambda task: None, partner_aic="repo-test",
                                        mode="local-repository-live", background_execution=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            health = (await client.get("/health")).json()
        self.assertEqual(health["skills"], ["repository.analyze"])

    async def test_invoke_business_whitelist_uses_official_lifecycle(self):
        async def execute(task): return repository_result()
        app = create_local_business_app(execute=execute, partner_aic="repo-test",
                                        mode="local-repository-live", background_execution=True)
        outcome = await invoke_business(payload(), "http://127.0.0.1/rpc", "repo-test",
                                        expected_task_type="repository_analyze",
                                        transport=httpx.ASGITransport(app=app), poll_seconds=.001)
        self.assertIsNone(outcome["error"])
        self.assertEqual(outcome["execution"]["state"], "completed")

    async def test_empty_url_partner_rejection_never_calls_agent(self):
        called = []
        async def execute(task): called.append(True); return repository_result()
        app = create_local_business_app(execute=execute, partner_aic="repo-test",
                                        mode="local-repository-live", background_execution=True)
        client = AipRpcClient("http://127.0.0.1/rpc", "local-repository-leader",
                              identity_binding_enabled=False, transport=httpx.ASGITransport(app=app))
        bad = payload(); bad["input"]["repositoryUrls"] = []
        try:
            response = await sdk_sender(client)(TaskCommand(
                id=str(uuid4()), sentAt=datetime.now(timezone.utc).isoformat(),
                senderRole="leader", senderId="local-repository-leader", sessionId=str(uuid4()),
                taskId="task-" + str(uuid4()), command=TaskCommandType.Start,
                dataItems=[StructuredDataItem(data=bad)]))
        finally:
            await client.close()
        self.assertEqual(response.status.state.value, "rejected")
        self.assertEqual(response.status.dataItems[0].text, "REPOSITORY_UNAVAILABLE")
        self.assertFalse(called)

    async def test_local_resolver_official_lifecycle_and_leader_validation(self):
        calls = []
        class Agent:
            def analyze(self, request):
                calls.append(request.model_dump())
                return SimpleNamespace(model_dump=lambda **kwargs: repository_result())
        app = create_local_business_app(
            execute=make_repository_executor(Agent()), partner_aic="repo-test",
            mode="local-repository-live", background_execution=True)
        client = AipRpcClient("http://127.0.0.1/rpc", "local-repository-leader",
                              identity_binding_enabled=False, transport=httpx.ASGITransport(app=app))
        try:
            resolver = LocalAipResolver({"repository.analyze": ("repo-test", sdk_sender(client), 5, .001)})
            result = await AipOrchestrator(resolver).run_plan(
                repository_plan(["https://github.com/pypa/sampleproject"]))
        finally:
            await client.close()
        record = result["executionRecords"][0]
        self.assertTrue(record["succeeded"])
        self.assertEqual(record["aipState"], "completed")
        self.assertTrue(record["aipTaskId"])
        self.assertEqual([event["command"] for event in record["aipReceipt"]["execution"]["events"]][-1:], ["complete"])
        self.assertEqual(len(result["results"][0]["items"]), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0]), set(payload()))
        self.assertNotIn("requiredSkill", calls[0]); self.assertNotIn("dependsOn", calls[0])

    async def test_resolver_missing_skill(self):
        with self.assertRaises(NoMatchingAgentError):
            await LocalAipResolver({}).resolve("repository.analyze")

    async def test_backend_failure_does_not_make_product(self):
        class Agent:
            def analyze(self, request):
                return SimpleNamespace(model_dump=lambda **kwargs: {
                    "schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
                    "summary": "failed", "items": [], "warnings": ["REPOSITORY_CLONE_FAILED: offline"]})
        app = create_local_business_app(
            execute=make_repository_executor(Agent()), partner_aic="repo-test",
            mode="local-repository-live", background_execution=True)
        client = AipRpcClient("http://127.0.0.1/rpc", "local-repository-leader",
                              identity_binding_enabled=False, transport=httpx.ASGITransport(app=app))
        try:
            resolver = LocalAipResolver({"repository.analyze": ("repo-test", sdk_sender(client), 5, .001)})
            result = await AipOrchestrator(resolver).run_plan(
                repository_plan(["https://github.com/pypa/sampleproject"]))
        finally:
            await client.close()
        self.assertFalse(result["results"])
        self.assertEqual(result["executionRecords"][0]["error"]["code"], "PARTNER_FAILED")


if __name__ == "__main__":
    unittest.main()
