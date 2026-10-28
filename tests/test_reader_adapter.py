import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest

from reader_adapter import ReaderAdapterError, call_reader, normalize_reader_result, to_reader_request


def task():
    return {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read", "goal": "Read paper",
            "input": {"papers": [{"paperId": "p1", "title": "Example", "doi": None,
                "url": "https://example.invalid/p1", "pdfUrl": "https://example.invalid/p1.pdf", "repositoryUrls": []}]},
            "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1",
            "requiredSkill": "paper.analyze", "dependsOn": ["S1"]}


def result():
    paper = task()["input"]["papers"][0]
    return {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read", "summary": "Example analysis",
            "items": [{"kind": "paper_note", "data": {"source": paper, "method": "Method",
                "findings": "Finding", "limitations": "Limitation", "analysisLevel": "fulltext",
                "evidence": [{"sourceId": "p1", "url": paper["pdfUrl"], "locator": "page 2",
                              "supports": "The evidence supports a concrete conclusion."}]}}], "warnings": []}


class ReaderRequestTests(unittest.TestCase):
    def test_strips_internal_fields_without_mutation(self):
        payload = task(); before = deepcopy(payload)
        request = to_reader_request(payload)
        self.assertEqual(set(request), {"schemaVersion", "subtaskId", "taskType", "goal", "input", "constraints", "outputSchema"})
        self.assertEqual(payload, before)
        request["input"]["papers"][0]["title"] = "Changed"
        self.assertEqual(payload, before)

    def test_business_request_and_default_constraint(self):
        payload = task(); payload.pop("requiredSkill"); payload.pop("dependsOn"); payload["constraints"] = {}
        self.assertEqual(to_reader_request(payload)["constraints"], {"maxPapers": 1})

    def test_rejects_batch_instead_of_truncating(self):
        for two, limit in [(True, 1), (True, 2), (False, 2)]:
            payload = task(); payload["constraints"]["maxPapers"] = limit
            if two: payload["input"]["papers"] *= 2
            with self.subTest(two=two, limit=limit), self.assertRaisesRegex(ReaderAdapterError, "SINGLE_PAPER_REQUIRED"):
                to_reader_request(payload)

    def test_rejects_unbound_input(self):
        payload = task(); payload["input"]["papers"] = []
        with self.assertRaisesRegex(ReaderAdapterError, "INVALID_TASK"): to_reader_request(payload)

    def test_rejects_unsupported_constraint(self):
        payload = task(); payload["constraints"]["yearFrom"] = 2025
        with self.assertRaisesRegex(ReaderAdapterError, "UNSUPPORTED_CONSTRAINT"): to_reader_request(payload)

    def test_rejects_wrong_skill_and_schema(self):
        for key, value in [("requiredSkill", "literature.search"), ("schemaVersion", "0.1"), ("outputSchema", "paper_records.v1")]:
            payload = task(); payload[key] = value
            with self.subTest(key=key), self.assertRaises(ReaderAdapterError): to_reader_request(payload)


class ReaderResultTests(unittest.TestCase):
    def normalize(self, raw): return normalize_reader_result(to_reader_request(task()), raw)

    def test_success_preserves_supports_and_warnings(self):
        raw = result(); raw["warnings"] = ["PAPERQA_CITATION_FORMATTING_INCOMPLETE"]
        before = deepcopy(raw); out = self.normalize(raw)
        self.assertTrue(out["diagnostics"]["businessCompleted"])
        self.assertEqual(out["result"], before); self.assertEqual(raw, before)
        self.assertIsNone(out["error"])

    def test_empty_not_reading_success(self):
        for warnings, code in [(["FULL_TEXT_UNAVAILABLE"], "FULL_TEXT_UNAVAILABLE"), ([], "READER_EMPTY_RESULT")]:
            raw = result(); raw.update(items=[], warnings=warnings)
            out = self.normalize(raw)
            self.assertFalse(out["diagnostics"]["businessCompleted"])
            self.assertTrue(out["diagnostics"]["structureValid"])
            self.assertEqual(out["error"]["code"], code)

    def test_rejects_old_supports_type_and_blank(self):
        for value in [["findings"], "", " "]:
            raw = result(); raw["items"][0]["data"]["evidence"][0]["supports"] = value
            with self.subTest(value=value), self.assertRaises(ReaderAdapterError): self.normalize(raw)

    def test_rejects_wrong_task_or_paper(self):
        for edit in [lambda r: r.update(subtaskId="other"),
                     lambda r: r["items"][0]["data"]["source"].update(paperId="other"),
                     lambda r: r["items"][0]["data"]["evidence"][0].update(sourceId="other"),
                     lambda r: r["items"][0]["data"]["evidence"][0].update(url="https://example.invalid/other.pdf")]:
            raw = result(); edit(raw)
            with self.assertRaisesRegex(ReaderAdapterError, "INVALID_READER_RESULT"): self.normalize(raw)

    def test_rejects_mock_bypass_and_multiple_notes(self):
        raw = result(); raw["items"][0]["data"]["isMock"] = True
        with self.assertRaises(ReaderAdapterError): self.normalize(raw)
        raw = result(); raw["items"] *= 2
        with self.assertRaises(ReaderAdapterError): self.normalize(raw)

    def test_rejects_note_with_fulltext_unavailable_warning(self):
        raw = result(); raw["warnings"] = ["FULL_TEXT_UNAVAILABLE"]
        with self.assertRaises(ReaderAdapterError): self.normalize(raw)


class ReaderCallTests(unittest.IsolatedAsyncioTestCase):
    model = SimpleNamespace(model_validate=lambda value: value)

    async def test_call_receives_business_only_task(self):
        async def run(payload):
            self.assertNotIn("requiredSkill", payload); self.assertNotIn("dependsOn", payload)
            return SimpleNamespace(model_dump=lambda **kw: result())
        out = await call_reader(task(), SimpleNamespace(run=run), self.model)
        self.assertTrue(out["diagnostics"]["businessCompleted"])

    async def test_provider_error_is_safe_failure(self):
        async def run(payload): raise RuntimeError("private credential")
        out = await call_reader(task(), SimpleNamespace(run=run), self.model)
        self.assertEqual(out["error"]["code"], "READER_CALL_FAILED")
        self.assertIsNone(out["result"]); self.assertNotIn("private credential", str(out))
        self.assertEqual(out["diagnostics"]["failureStage"], "reader_agent_run")
        self.assertEqual(out["diagnostics"]["exceptionClass"], "RuntimeError")
        self.assertEqual(out["diagnostics"]["exceptionSummary"],
                         "Reader agent raised before producing a response")

    async def test_timeout(self):
        async def run(payload): await asyncio.sleep(10)
        out = await call_reader(task(), SimpleNamespace(run=run), self.model, timeout_seconds=0.01)
        self.assertEqual(out["error"]["code"], "READER_TIMEOUT")

    async def test_invalid_return_maps_to_failure(self):
        async def run(payload):
            raw = result(); raw["subtaskId"] = "wrong"
            return SimpleNamespace(model_dump=lambda **kw: raw)
        out = await call_reader(task(), SimpleNamespace(run=run), self.model)
        self.assertEqual(out["error"]["code"], "INVALID_READER_RESULT")

    async def test_invalid_timeout_does_not_call_reader(self):
        for value in [0, -1, True, float("nan"), float("inf"), "45"]:
            with self.subTest(value=value), self.assertRaisesRegex(ReaderAdapterError, "INVALID_TIMEOUT"):
                await call_reader(task(), None, self.model, timeout_seconds=value)

    async def test_cancellation_propagates(self):
        async def run(payload): raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await call_reader(task(), SimpleNamespace(run=run), self.model)
