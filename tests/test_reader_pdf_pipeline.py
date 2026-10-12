"""全文补全阶段及缓存续跑的离线回归；无模型、无网络。"""
import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from reader_batch_adapter import call_reader_batch
from run_research import run_pipeline, main, save_pipeline
from tests.test_reader_batch_adapter import RecordingReader
from tests.test_research_reader_pipeline import chain_plan, search_outcome

PDF = "https://arxiv.org/pdf/9999.12345"


class PdfPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plan = chain_plan()
        self.plan["tasks"][1]["constraints"]["maxPapers"] = 1
        outcome = search_outcome(self.plan)
        outcome["result"]["items"][0]["data"].update(pdfUrl=None, sourceUrls=["https://arxiv.org/abs/9999.12345"])
        self.planner = AsyncMock(return_value=(self.plan, {}))
        self.search = AsyncMock(return_value=outcome)
        self.reader = RecordingReader()
        self.resolution_calls = []

    async def resolve(self, task, result, *, previous=None):
        self.resolution_calls.append(deepcopy((task, previous)))
        resolved = deepcopy(task)
        resolved["input"]["papers"][0]["pdfUrl"] = PDF
        return resolved, [{"paperId": "p1", "state": "verified", "pdfUrl": PDF,
            "sourceUrl": "https://arxiv.org/abs/9999.12345", "cacheReused": bool(previous),
            "networkAttempted": not bool(previous)}]

    async def read(self, payload, **options):
        return await call_reader_batch(payload, self.reader, SimpleNamespace(model_validate=lambda x: x), **options)

    async def run_it(self, **options):
        return await run_pipeline(self.plan["userQuery"], planner_call=self.planner,
            search_call=self.search, reader_call=self.read, **options)

    async def test_fresh_missing_pdf_resolved_before_reader_without_mutating_search(self):
        before = deepcopy(self.search.return_value)
        record = await self.run_it(pdf_resolver=self.resolve)
        self.assertEqual(record["metadata"]["pipelineOutcome"], "completed")
        self.assertEqual(self.reader.calls[0]["input"]["papers"][0]["pdfUrl"], PDF)
        self.assertEqual(record["outcome"], before)
        self.assertIsNone(record["readerTaskBeforePdfResolution"]["input"]["papers"][0]["pdfUrl"])

    async def test_preparation_only_skips_planner_search_and_reader(self):
        failed = await self.run_it()
        self.planner.reset_mock(); self.search.reset_mock(); self.reader = RecordingReader()
        record = await self.run_it(saved_plan=self.plan, saved_record=failed,
                                   pdf_resolver=self.resolve, prepare_sources_only=True)
        self.planner.assert_not_awaited(); self.search.assert_not_awaited()
        self.assertEqual(self.reader.calls, [])
        self.assertEqual(record["metadata"]["pipelineOutcome"], "reader-ready")
        self.assertFalse(record["metadata"]["llmUsed"])
        self.assertFalse(record["metadata"]["paperReadingExecuted"])
        self.assertIsNone(record["readerOutcome"])

    async def test_prepared_checkpoint_resume_retains_enrichment_and_skips_planner_search(self):
        failed = await self.run_it()
        prepared = await self.run_it(saved_plan=self.plan, saved_record=failed,
                                    pdf_resolver=self.resolve, prepare_sources_only=True)
        before = deepcopy(prepared)
        self.planner.reset_mock(); self.search.reset_mock(); self.reader = RecordingReader()
        record = await self.run_it(saved_plan=self.plan, saved_record=prepared, pdf_resolver=self.resolve)
        self.assertEqual(record["metadata"]["pipelineOutcome"], "completed")
        self.assertTrue(record["pdfResolution"][0]["cacheReused"])
        self.assertFalse(record["metadata"]["pdfResolutionNetworkUsed"])
        self.assertEqual(len(self.reader.calls), 1)
        self.planner.assert_not_awaited(); self.search.assert_not_awaited()
        self.assertEqual(prepared, before)

    async def test_completed_enriched_checkpoint_does_not_repeat_reader(self):
        saved = await self.run_it(pdf_resolver=self.resolve)
        self.reader = RecordingReader(); self.planner.reset_mock(); self.search.reset_mock()
        record = await self.run_it(saved_plan=self.plan, saved_record=saved, pdf_resolver=self.resolve)
        self.assertEqual(record["metadata"]["pipelineOutcome"], "completed")
        self.assertEqual(self.reader.calls, [])
        self.assertFalse(record["metadata"]["llmUsed"])

    async def test_title_or_goal_changes_cannot_bypass_checkpoint_validation(self):
        saved = await self.run_it()
        for mutate in [lambda r: r["readerTask"].update(goal="changed"),
                       lambda r: r["readerTask"]["input"]["papers"][0].update(title="changed")]:
            bad = deepcopy(saved); mutate(bad)
            resolver = AsyncMock()
            self.reader = RecordingReader()
            record = await self.run_it(saved_plan=self.plan, saved_record=bad, pdf_resolver=resolver)
            resolver.assert_not_awaited()
            self.assertEqual(record["error"]["code"], "INVALID_READER_CHECKPOINT")
            self.assertEqual(self.reader.calls, [])

    async def test_resolver_cannot_change_goal_or_existing_pdf(self):
        async def bad(task, result, **kwargs):
            task = deepcopy(task); task["goal"] = "changed"
            return task, []
        record = await self.run_it(pdf_resolver=bad)
        self.assertEqual(record["error"]["code"], "INVALID_PDF_ENRICHMENT")
        self.assertEqual(self.reader.calls, [])

    async def test_prepare_without_successful_checkpoint_does_not_execute_anything(self):
        record = await self.run_it(pdf_resolver=self.resolve, prepare_sources_only=True)
        self.assertEqual(record["error"]["code"], "PDF_PREPARATION_REQUIRES_SUCCESSFUL_SEARCH")
        self.planner.assert_not_awaited(); self.search.assert_not_awaited()
        self.assertEqual(self.reader.calls, [])

    async def test_unavailable_source_retains_null_and_stops_preparation(self):
        failed = await self.run_it()
        async def unavailable(task, result, **kwargs):
            return deepcopy(task), [{"paperId": "p1", "state": "unavailable", "reason": "NO_KNOWN_ARXIV_SOURCE"}]
        self.reader = RecordingReader()
        record = await self.run_it(saved_plan=self.plan, saved_record=failed,
                                  pdf_resolver=unavailable, prepare_sources_only=True)
        self.assertEqual(record["error"]["code"], "PDF_SOURCE_RESOLUTION_INCOMPLETE")
        self.assertEqual(self.reader.calls, [])
        self.assertIsNone(record["readerTask"]["input"]["papers"][0]["pdfUrl"])

    async def test_resolution_cancellation_preserves_prior_successes(self):
        self.plan["tasks"][1]["constraints"]["maxPapers"] = 3
        saved = await self.run_it()
        async def cancel(*args, **kwargs): raise asyncio.CancelledError()
        snapshots = []
        with self.assertRaises(asyncio.CancelledError):
            await self.run_it(saved_plan=self.plan, saved_record=saved, pdf_resolver=cancel,
                              on_progress=lambda r: snapshots.append(deepcopy(r)))
        self.assertEqual([p["paperId"] for p in snapshots[-1]["readerProgress"]], ["p2", "p3"])
        self.assertEqual(snapshots[-1]["metadata"]["pipelineOutcome"], "interrupted")


class PreparationEntryTests(unittest.TestCase):
    def test_preparation_flag_without_checkpoint_rejected(self):
        with patch("run_research.prepare_live_reader") as reader, patch("run_research.create_plan") as planner, patch("builtins.print"):
            self.assertEqual(main(["--query", "test", "--with-reader", "--reader-root", "unused", "--prepare-reader-sources"]), 1)
            reader.assert_not_called(); planner.assert_not_called()

    def test_prepare_cli_does_not_load_keys_or_providers_and_preserves_history(self):
        plan = chain_plan()
        outcome = search_outcome(plan)
        async def no_read(*args, **kwargs): raise AssertionError("must not read")
        saved = asyncio.run(run_pipeline(plan["userQuery"], planner_call=AsyncMock(return_value=(plan, {})),
            search_call=AsyncMock(return_value=outcome), reader_call=no_read, prepare_sources_only=False))
        # 构造保存过检索、尚未阅读的检查点，已有 PDF 地址不需网络核验。
        saved["readerProgress"] = []
        with tempfile.TemporaryDirectory() as directory:
            history = save_pipeline(saved, Path(directory) / "history")
            original = (history / "pipeline.json").read_bytes()
            with patch("run_research.ROOT", Path(directory)), patch("run_research.prepare_live_reader") as reader, \
                    patch("run_research.prepare_live_wrapper") as literature, \
                    patch("run_research.ProviderSettings.from_env") as config, \
                    patch("run_research.create_plan", new_callable=AsyncMock) as planner, \
                    patch("run_research.execute_live_task", new_callable=AsyncMock) as search, patch("builtins.print"):
                self.assertEqual(main(["--resume", str(history), "--with-reader", "--reader-root", "unused", "--prepare-reader-sources"]), 0)
                reader.assert_not_called(); literature.assert_not_called(); config.assert_not_called()
                planner.assert_not_awaited(); search.assert_not_awaited()
            self.assertEqual((history / "pipeline.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
