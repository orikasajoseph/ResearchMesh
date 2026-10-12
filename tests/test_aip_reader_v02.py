"""Offline tests for independent v0.2 Reader AIP adapter (no LLM, PDF, Internet)."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import unittest

import httpx

from agents.paper_reader.researchmesh_paper_reader.agent import PaperReaderAgent
from agents.paper_reader.researchmesh_paper_reader.provider import ProviderAnalysis, ProviderContext
from agents.paper_reader.researchmesh_paper_reader.schemas import PaperReadTask
from aip.paper_reader_v02_bridge import (
    business_task_from_planner, create_reader_local_app, invoke_reader_aip,
    validate_reader_result,
)
from aip.wu_delivery.structured import ResearchClient


PAPER = {
    "paperId": "test-paper-123",
    "title": "Offline fixture only",
    "doi": "10.1234/example",
    "url": "https://example.org/paper",
    "pdfUrl": "https://example.org/sample.pdf",
    "repositoryUrls": [],
}


def make_task(pdf=True):
    paper = deepcopy(PAPER)
    if not pdf:
        paper["pdfUrl"] = None
    return {
        "schemaVersion": "0.2", "subtaskId": "S-1", "taskType": "paper_read",
        "goal": "Offline schema validation", "input": {"papers": [paper]},
        "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1",
        "requiredSkill": "paper.analyze", "dependsOn": [],
    }


class FakeProvider:
    async def analyze(self, paper):
        return ProviderAnalysis(
            answer=("RESEARCH_PROBLEM: Research problem placeholder.\n"
                    "METHOD: The method cites section-1.\n"
                    "FINDINGS: Fixture findings cite section-1.\n"
                    "LIMITATIONS: Fixture limitations cite section-1."),
            contexts=[ProviderContext(
                context_id="pqac-a1b2c3d4", locator="section-1",
                raw_text="Fixture extracted context (not a real PDF)",
                summary="Mock evidence for fixture testing", score=1.0,
                source="Offline simulated citation",
            )],
            references="", timings={}, diagnostics={},
        )


class UnexpectedProvider:
    async def analyze(self, paper):
        raise AssertionError("PDF should not be accessed when pdfUrl is null")


async def roundtrip(task, agent):
    app = create_reader_local_app(agent)
    client = ResearchClient(
        partner_url="http://127.0.0.1:8011/rpc", leader_id="reader-v02-unit-test",
        identity_binding_enabled=False, transport=httpx.ASGITransport(app=app),
    )
    try:
        return await invoke_reader_aip(client, business_task_from_planner(task))
    finally:
        await client.close()


class ReaderV02BridgeTests(unittest.TestCase):
    def test_only_seven_business_fields_sent(self):
        task = business_task_from_planner(make_task())
        self.assertEqual(len(task.model_dump()), 7)
        self.assertNotIn("requiredSkill", task.model_dump())
        self.assertNotIn("dependsOn", task.model_dump())
        self.assertEqual(task.schemaVersion, "0.2")

    def test_wrong_skill_or_dependency_rejected(self):
        wrong = make_task()
        wrong["requiredSkill"] = "literature.search"
        with self.assertRaisesRegex(ValueError, "SKILL_MISMATCH"):
            business_task_from_planner(wrong)
        wrong = make_task()
        wrong["dependsOn"] = ["S0"]
        with self.assertRaisesRegex(ValueError, "DEPENDENCIES_NOT_EXECUTED"):
            business_task_from_planner(wrong)

    def test_invalid_paper_quantity_rejected(self):
        invalid = make_task()
        invalid["input"]["papers"].append(deepcopy(PAPER))
        with self.assertRaises(Exception):
            business_task_from_planner(invalid)

    def test_offline_no_pdf_protocol_without_research(self):
        outcome = asyncio.run(roundtrip(make_task(pdf=False), PaperReaderAgent(provider=UnexpectedProvider())))
        self.assertEqual(outcome.status, "completed")
        self.assertTrue(outcome.protocol_completed)
        self.assertTrue(outcome.business_validated)
        self.assertFalse(outcome.research_succeeded)
        self.assertEqual(outcome.result["warnings"], ["FULL_TEXT_UNAVAILABLE"])
        self.assertEqual(outcome.result["items"], [])

    def test_fixture_with_pdf_structure_and_aip_handshake(self):
        # This uses a fake provider. A positive result here is NOT proof of real research.
        outcome = asyncio.run(roundtrip(make_task(), PaperReaderAgent(provider=FakeProvider())))
        self.assertEqual(outcome.status, "completed")
        self.assertTrue(outcome.protocol_completed)
        self.assertTrue(outcome.business_validated)
        self.assertFalse(outcome.research_succeeded)  # Fake provider must not count as real research
        self.assertIsNone(outcome.error_code)
        self.assertEqual(outcome.result["schemaVersion"], "0.2")
        self.assertEqual(outcome.result["items"][0]["kind"], "paper_note")
        self.assertIsInstance(outcome.result["items"][0]["data"]["evidence"][0]["supports"], str)

    def test_evidence_url_required_by_leader(self):
        task = business_task_from_planner(make_task())
        from agents.paper_reader.researchmesh_paper_reader.schemas import PaperReadResult
        good = asyncio.run(PaperReaderAgent(provider=FakeProvider()).run(task))
        raw = good.model_dump(mode="json")
        raw["items"][0]["data"]["evidence"][0]["url"] = None
        with self.assertRaises(Exception):
            validate_reader_result(PaperReadResult.model_validate(raw), task)


if __name__ == "__main__":
    unittest.main()
