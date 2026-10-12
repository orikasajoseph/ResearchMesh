import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest

from reader_adapter import ReaderAdapterError
from reader_batch_adapter import call_reader_batch, prepare_reader_batch
from result_contract import validate_result


def batch_task():
    return {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read", "goal": "Read three papers",
            "input": {"papers": [{"paperId": f"p{i}", "title": f"Paper {i}", "doi": None,
                "url": f"https://example.invalid/p{i}", "pdfUrl": f"https://example.invalid/p{i}.pdf", "repositoryUrls": []}
                for i in range(1, 4)]}, "constraints": {"maxPapers": 3}, "outputSchema": "paper_notes.v1",
            "requiredSkill": "paper.analyze", "dependsOn": ["S1"]}


class RecordingReader:
    def __init__(self, modes=None): self.modes = modes or {}; self.calls = []
    async def run(self, payload):
        self.calls.append(deepcopy(payload))
        paper = payload["input"]["papers"][0]
        mode = self.modes.get(paper["paperId"], "success")
        if mode == "error": raise RuntimeError("private credential")
        if mode == "timeout": await asyncio.sleep(10)
        if mode == "cancel": raise asyncio.CancelledError()
        raw = {"schemaVersion": "0.2", "subtaskId": payload["subtaskId"], "taskType": "paper_read",
               "summary": "Synthetic test analysis", "items": [], "warnings": []}
        if mode == "missing" or paper["pdfUrl"] is None:
            raw["warnings"] = ["FULL_TEXT_UNAVAILABLE"]
        elif mode != "empty":
            raw["items"] = [{"kind": "paper_note", "data": {"source": deepcopy(paper),
                "method": "Method", "findings": f"Finding for {paper['paperId']}", "limitations": "Limitation",
                "analysisLevel": "fulltext", "evidence": [{"sourceId": paper["paperId"], "url": paper["pdfUrl"],
                    "locator": "page 2", "supports": f"Concrete conclusion for {paper['paperId']}"}]}}]
        if mode == "wrong_id": raw["subtaskId"] = "other"
        if mode == "wrong_source": raw["items"][0]["data"]["source"]["paperId"] = "other"
        if mode == "legacy_supports": raw["items"][0]["data"]["evidence"][0]["supports"] = ["findings"]
        if mode == "warning": raw["warnings"] = ["PAPERQA_CITATION_FORMATTING_INCOMPLETE"]
        return SimpleNamespace(model_dump=lambda **kw: deepcopy(raw))


class BatchPreparationTests(unittest.TestCase):
    def test_split_retains_order_and_does_not_mutate(self):
        payload = batch_task(); before = deepcopy(payload)
        parent, children = prepare_reader_batch(payload)
        self.assertEqual(payload, before)
        self.assertEqual(parent["subtaskId"], "S2")
        self.assertEqual([c["subtaskId"] for c in children], ["S2/paper-001", "S2/paper-002", "S2/paper-003"])
        for index, child in enumerate(children):
            self.assertEqual(child["constraints"], {"maxPapers": 1})
            self.assertEqual(child["input"]["papers"], [before["input"]["papers"][index]])
            self.assertNotIn("requiredSkill", child); self.assertNotIn("dependsOn", child)
        children[0]["input"]["papers"][0]["title"] = "Changed"
        self.assertEqual(payload, before); self.assertEqual(parent["input"], before["input"])

    def test_defaults_and_single_input(self):
        payload = batch_task(); payload["constraints"] = {}
        self.assertEqual(prepare_reader_batch(payload)[0]["constraints"], {"maxPapers": 3})
        payload["input"]["papers"] = payload["input"]["papers"][:1]
        payload["constraints"] = {"maxPapers": 1}
        self.assertEqual(len(prepare_reader_batch(payload)[1]), 1)

    def test_over_limit_rejected_without_truncation(self):
        payload = batch_task(); payload["constraints"]["maxPapers"] = 2
        with self.assertRaisesRegex(ReaderAdapterError, "TOO_MANY_PAPERS"): prepare_reader_batch(payload)

    def test_duplicates_rejected(self):
        for field in ["paperId", "url"]:
            payload = batch_task(); payload["input"]["papers"][1][field] = payload["input"]["papers"][0][field]
            with self.subTest(field=field), self.assertRaisesRegex(ReaderAdapterError, "DUPLICATE_PAPER"):
                prepare_reader_batch(payload)

    def test_empty_or_invalid_later_paper_rejected(self):
        payload = batch_task(); payload["input"]["papers"] = []
        with self.assertRaises(ReaderAdapterError): prepare_reader_batch(payload)
        payload = batch_task(); payload["input"]["papers"][2]["pdfUrl"] = "invalid"
        with self.assertRaises(ReaderAdapterError): prepare_reader_batch(payload)

    def test_unsupported_contract_rejected(self):
        for key, value in [("requiredSkill", "literature.search"), ("schemaVersion", "0.1"),
                           ("outputSchema", "paper_records.v1"), ("constraints", {"maxPapers": 3, "yearFrom": 2025})]:
            payload = batch_task(); payload[key] = value
            with self.subTest(key=key), self.assertRaises(ReaderAdapterError): prepare_reader_batch(payload)


class BatchCallTests(unittest.IsolatedAsyncioTestCase):
    model = SimpleNamespace(model_validate=lambda payload: payload)

    async def run_batch(self, modes=None, payload=None, timeout_seconds=1):
        reader = RecordingReader(modes)
        out = await call_reader_batch(payload or batch_task(), reader, self.model, timeout_seconds=timeout_seconds)
        return out, reader

    async def test_all_success(self):
        payload = batch_task(); before = deepcopy(payload)
        out, reader = await self.run_batch(payload=payload)
        self.assertEqual(payload, before)
        self.assertEqual(out["diagnostics"], {"outcome": "success", "businessCompleted": True,
            "requestedCount": 3, "completedCount": 3, "failedCount": 0})
        self.assertIsNone(out["error"])
        self.assertEqual(out["result"]["subtaskId"], "S2")
        self.assertEqual([i["data"]["source"]["paperId"] for i in out["result"]["items"]], ["p1", "p2", "p3"])
        self.assertEqual(len(reader.calls), 3)
        validate_result(out["result"], payload)

    async def test_partial_retains_good_results_after_middle_error(self):
        out, reader = await self.run_batch({"p2": "error"})
        self.assertEqual(out["diagnostics"]["outcome"], "partial")
        self.assertFalse(out["diagnostics"]["businessCompleted"])
        self.assertEqual(out["error"]["code"], "PARTIAL_READER_FAILURE")
        self.assertEqual([i["data"]["source"]["paperId"] for i in out["result"]["items"]], ["p1", "p3"])
        self.assertEqual(out["paperOutcomes"][1]["error"]["code"], "READER_CALL_FAILED")
        self.assertEqual(len(reader.calls), 3)
        self.assertNotIn("private credential", str(out))

    async def test_all_failed_has_no_fabricated_notes(self):
        out, reader = await self.run_batch({"p1": "missing", "p2": "error", "p3": "empty"})
        self.assertEqual(out["diagnostics"]["outcome"], "failed")
        self.assertEqual(out["diagnostics"]["completedCount"], 0)
        self.assertEqual(out["error"]["code"], "ALL_READERS_FAILED")
        self.assertEqual(out["result"]["items"], [])
        self.assertEqual([p["error"]["code"] for p in out["paperOutcomes"]],
            ["FULL_TEXT_UNAVAILABLE", "READER_CALL_FAILED", "READER_EMPTY_RESULT"])
        self.assertEqual(len(reader.calls), 3)

    async def test_timeout_does_not_stop_next_paper(self):
        out, reader = await self.run_batch({"p2": "timeout"}, timeout_seconds=0.01)
        self.assertEqual(out["paperOutcomes"][1]["error"]["code"], "READER_TIMEOUT")
        self.assertEqual(out["diagnostics"]["completedCount"], 2)
        self.assertEqual(len(reader.calls), 3)

    async def test_bad_result_is_isolated(self):
        for mode in ["wrong_id", "wrong_source", "legacy_supports"]:
            out, reader = await self.run_batch({"p2": mode})
            with self.subTest(mode=mode):
                self.assertEqual(out["paperOutcomes"][1]["error"]["code"], "INVALID_READER_RESULT")
                self.assertEqual(out["diagnostics"]["completedCount"], 2)

    async def test_warning_and_evidence_preserved_with_paper_identity(self):
        out, reader = await self.run_batch({"p2": "warning"})
        self.assertIn("S2/paper-002: PAPERQA_CITATION_FORMATTING_INCOMPLETE", out["result"]["warnings"])
        self.assertEqual(out["result"]["items"][1]["data"]["evidence"][0]["supports"], "Concrete conclusion for p2")
        self.assertEqual(out["paperOutcomes"][1]["result"]["warnings"], ["PAPERQA_CITATION_FORMATTING_INCOMPLETE"])
        self.assertTrue(out["diagnostics"]["businessCompleted"])

    async def test_preflight_checks_entire_batch_before_call(self):
        def validate(payload):
            if payload["input"]["papers"][0]["paperId"] == "p3": raise ValueError("secret diagnostic")
            return payload
        reader = RecordingReader()
        with self.assertRaisesRegex(ReaderAdapterError, "INVALID_READER_TASK"):
            await call_reader_batch(batch_task(), reader, SimpleNamespace(model_validate=validate))
        self.assertEqual(reader.calls, [])

    async def test_invalid_timeout_never_calls_reader(self):
        reader = RecordingReader()
        for timeout in [0, -1, True, float("nan"), float("inf")]:
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ReaderAdapterError, "INVALID_TIMEOUT"):
                await call_reader_batch(batch_task(), reader, self.model, timeout_seconds=timeout)
        self.assertEqual(reader.calls, [])

    async def test_cancel_propagates_without_calling_remaining_papers(self):
        reader = RecordingReader({"p2": "cancel"})
        with self.assertRaises(asyncio.CancelledError): await call_reader_batch(batch_task(), reader, self.model)
        self.assertEqual(len(reader.calls), 2)
