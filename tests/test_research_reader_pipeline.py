"""检索 → Reader、取消与显式续跑的离线行为回归；不联网、不读 Key。"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from literature_adapter import normalize_response, to_wrapper_request
from reader_batch_adapter import call_reader_batch
from run_research import (run_pipeline, save_pipeline, render_pipeline_report,
                          load_resume_plan, load_resume_record, main)
from tests.test_literature_adapter import plan, response
from tests.test_reader_batch_adapter import batch_task, RecordingReader


def chain_plan():
    candidate = plan()
    search = candidate["tasks"][0]
    search["constraints"] = {"maxResults": 3}
    reader = batch_task()
    reader.update(subtaskId="Reader", dependsOn=[search["subtaskId"]], input={"papers": []})
    candidate["tasks"].append(reader)
    return candidate


def search_outcome(candidate):
    task = {key: value for key, value in candidate["tasks"][0].items()
            if key not in ("dependsOn", "requiredSkill")}
    raw = response()
    paper = raw["papers"][0]
    raw["papers"] = []
    for index in range(1, 4):
        current = deepcopy(paper)
        current.update(paperId=f"p{index}", title=f"Paper {index}", doi=None,
                       url=f"https://example.invalid/p{index}", pdfUrl=f"https://example.invalid/p{index}.pdf",
                       repositoryUrls=[])
        raw["papers"].append(current)
    raw["total"] = 3
    return normalize_response(task, to_wrapper_request(task, "test-request"), raw)


class ReaderPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plan = chain_plan()
        self.planner = AsyncMock(return_value=(self.plan, {"requestCount": 1}))
        self.search = AsyncMock(return_value=search_outcome(self.plan))
        self.agent = RecordingReader()

    async def read(self, payload, **options):
        return await call_reader_batch(payload, self.agent, SimpleNamespace(model_validate=lambda data: data),
                                       timeout_seconds=.02, **options)

    async def run_it(self, **options):
        return await run_pipeline(self.plan["userQuery"], planner_call=self.planner,
                                  search_call=self.search, reader_call=self.read, **options)

    async def test_binds_real_payload_shape_and_preserves_inputs(self):
        before = deepcopy(self.plan)
        record = await self.run_it()
        self.assertEqual(record["metadata"]["pipelineOutcome"], "completed")
        self.assertEqual(len(record["readerOutcome"]["result"]["items"]), 3)
        self.assertEqual(record["readerOutcome"]["result"]["subtaskId"], "Reader")
        self.assertEqual([call["input"]["papers"][0]["paperId"] for call in self.agent.calls], ["p1", "p2", "p3"])
        for call in self.agent.calls:
            self.assertEqual(call["constraints"], {"maxPapers": 1})
            self.assertNotIn("requiredSkill", call); self.assertNotIn("dependsOn", call)
        self.assertFalse(record["metadata"]["aipUsed"])
        self.assertEqual(self.plan, before)
        self.assertIn("Concrete conclusion for p1", render_pipeline_report(record))

    async def test_selection_limit_reported(self):
        self.plan["tasks"][1]["constraints"]["maxPapers"] = 1
        record = await self.run_it()
        self.assertEqual(len(self.agent.calls), 1)
        self.assertEqual(record["metadata"]["retrievedCount"], 3)
        self.assertEqual(record["metadata"]["selectedPaperCount"], 1)
        self.assertIn("选择 1 篇", render_pipeline_report(record))

    async def test_explicit_papers_take_priority(self):
        self.plan["tasks"][1]["input"] = {"papers": [batch_task()["input"]["papers"][2]]}
        record = await self.run_it()
        self.assertEqual(record["readerTask"]["input"]["papers"][0]["paperId"], "p3")
        self.assertEqual(len(self.agent.calls), 1)

    async def test_unsupported_reader_constraint_before_search(self):
        self.plan["tasks"][1]["constraints"]["unknown"] = True
        record = await self.run_it()
        self.assertEqual(record["error"]["code"], "UNSUPPORTED_CONSTRAINT")
        self.search.assert_not_awaited(); self.assertEqual(self.agent.calls, [])

    async def test_complex_plan_rejected_before_search(self):
        extra = deepcopy(self.plan["tasks"][0]); extra["subtaskId"] = "Extra"
        self.plan["tasks"].append(extra)
        record = await self.run_it()
        self.assertEqual(record["error"]["code"], "ONLY_LITERATURE_READER_CHAIN_SUPPORTED")
        self.search.assert_not_awaited(); self.assertEqual(self.agent.calls, [])

    async def test_clarification_no_agents(self):
        self.plan.update(needsClarification=True, clarificationQuestion="请指定研究主题", tasks=[])
        record = await self.run_it()
        self.assertEqual(record["metadata"]["pipelineOutcome"], "needs-clarification")
        self.assertIn("请指定研究主题", render_pipeline_report(record))
        self.search.assert_not_awaited(); self.assertEqual(self.agent.calls, [])

    async def test_empty_search_skips_reader(self):
        self.search.return_value["result"]["items"] = []
        record = await self.run_it()
        self.assertEqual(record["metadata"]["pipelineOutcome"], "empty")
        self.assertFalse(record["metadata"]["paperReadingExecuted"])
        self.assertEqual(self.agent.calls, [])

    async def test_failed_search_skips_reader(self):
        self.search.return_value = {"result": None, "error": {"code": "RATE_LIMITED"}, "diagnostics": {}}
        record = await self.run_it()
        self.assertEqual(record["error"]["code"], "RATE_LIMITED")
        self.assertEqual(record["metadata"]["pipelineOutcome"], "retrieval-failed")
        self.assertEqual(self.agent.calls, [])

    async def test_partial_retains_notes_and_failure(self):
        self.agent.modes = {"p2": "missing"}
        record = await self.run_it()
        self.assertEqual(record["metadata"]["pipelineOutcome"], "partial")
        self.assertFalse(record["readerOutcome"]["diagnostics"]["businessCompleted"])
        self.assertEqual([item["data"]["source"]["paperId"] for item in record["readerOutcome"]["result"]["items"]], ["p1", "p3"])
        self.assertIn("FULL_TEXT_UNAVAILABLE", render_pipeline_report(record))

    async def test_all_failed_has_no_notes(self):
        self.agent.modes = {"p1": "error", "p2": "timeout", "p3": "missing"}
        record = await self.run_it()
        self.assertEqual(record["metadata"]["pipelineOutcome"], "reading-failed")
        self.assertEqual(record["readerOutcome"]["result"]["items"], [])
        self.assertNotIn("private credential", json.dumps(record))
        self.assertEqual([paper["error"]["code"] for paper in record["readerProgress"]],
                         ["READER_CALL_FAILED", "READER_TIMEOUT", "FULL_TEXT_UNAVAILABLE"])

    async def test_checkpoint_search_saved_before_reader_and_each_paper(self):
        snapshots = []
        def checkpoint(record):
            snapshots.append(deepcopy(record))
        record = await self.run_it(on_progress=checkpoint)
        ready = next(snapshot for snapshot in snapshots if snapshot["metadata"]["pipelineOutcome"] == "reader-ready")
        self.assertIsNotNone(ready["outcome"]["result"])
        self.assertEqual(ready["readerProgress"], [])
        self.assertEqual([len(s["readerProgress"]) for s in snapshots if len(s["readerProgress"])], [1, 1, 2, 2, 3, 3])
        self.assertEqual(record["metadata"]["pipelineOutcome"], "completed")

    async def test_cancel_saves_success_and_resume_only_unfinished(self):
        self.agent.modes = {"p2": "cancel"}
        with tempfile.TemporaryDirectory() as directory:
            outputs = []
            def initial(record): outputs.append(save_pipeline(record, directory))
            def progress(record): save_pipeline(record, output_dir=outputs[0])
            with self.assertRaises(asyncio.CancelledError):
                await self.run_it(on_plan=initial, on_progress=progress)
            saved_plan = load_resume_plan(outputs[0], with_reader=True)
            saved = load_resume_record(outputs[0], saved_plan)
            self.assertEqual(saved["metadata"]["pipelineOutcome"], "interrupted")
            self.assertEqual([p["paperId"] for p in saved["readerProgress"]], ["p1"])
            self.agent = RecordingReader()
            self.search.reset_mock(); self.planner.reset_mock()
            record = await self.run_it(saved_plan=saved_plan, saved_record=saved)
            self.planner.assert_not_awaited(); self.search.assert_not_awaited()
            self.assertEqual([call["input"]["papers"][0]["paperId"] for call in self.agent.calls], ["p2", "p3"])
            self.assertEqual(record["readerOutcome"]["diagnostics"]["reusedCount"], 1)
            self.assertEqual(record["metadata"]["pipelineOutcome"], "completed")

    async def test_partial_resume_keeps_later_success_if_cancelled_again(self):
        self.agent.modes = {"p2": "missing"}
        saved = await self.run_it()
        self.agent = RecordingReader({"p2": "cancel"})
        snapshots = []
        with self.assertRaises(asyncio.CancelledError):
            await self.run_it(saved_plan=self.plan, saved_record=saved,
                              on_progress=lambda r: snapshots.append(deepcopy(r)))
        self.assertEqual([p["paperId"] for p in snapshots[-1]["readerProgress"]], ["p1", "p3"])

    async def test_partial_resume_retries_failure_only(self):
        self.agent.modes = {"p2": "error"}
        saved = await self.run_it(); before = deepcopy(saved)
        self.agent = RecordingReader(); self.planner.reset_mock(); self.search.reset_mock()
        record = await self.run_it(saved_plan=self.plan, saved_record=saved)
        self.assertEqual([call["input"]["papers"][0]["paperId"] for call in self.agent.calls], ["p2"])
        self.planner.assert_not_awaited(); self.search.assert_not_awaited()
        self.assertEqual(record["readerOutcome"]["diagnostics"]["reusedCount"], 2)
        self.assertEqual(saved, before)

    async def test_completed_resume_no_model_or_search_calls(self):
        saved = await self.run_it()
        self.agent = RecordingReader(); self.planner.reset_mock(); self.search.reset_mock()
        record = await self.run_it(saved_plan=self.plan, saved_record=saved)
        self.assertEqual(self.agent.calls, [])
        self.assertFalse(record["metadata"]["llmUsed"])
        self.assertFalse(record["metadata"]["paperReadingExecuted"])
        self.assertFalse(record["metadata"]["realSourceRequested"])
        self.assertFalse(record["metadata"]["agentsExecuted"])
        self.assertEqual(record["metadata"]["pipelineOutcome"], "completed")
        self.search.assert_not_awaited(); self.planner.assert_not_awaited()

    async def test_reader_source_changed_checkpoint_rejected_before_calls(self):
        saved = await self.run_it()
        saved["readerProgress"][2]["result"]["items"][0]["data"]["source"]["pdfUrl"] = "https://example.invalid/other.pdf"
        self.agent = RecordingReader(); self.search.reset_mock()
        record = await self.run_it(saved_plan=self.plan, saved_record=saved)
        self.assertEqual(record["error"]["code"], "INVALID_READER_CHECKPOINT")
        self.assertEqual(self.agent.calls, []); self.search.assert_not_awaited()

    async def test_changed_reader_task_rejected(self):
        saved = await self.run_it(); saved["readerTask"]["goal"] = "changed"
        self.agent = RecordingReader()
        record = await self.run_it(saved_plan=self.plan, saved_record=saved)
        self.assertEqual(record["error"]["code"], "INVALID_READER_CHECKPOINT")
        self.assertEqual(self.agent.calls, [])

    async def test_duplicate_checkpoint_rejected_before_calls(self):
        saved = await self.run_it()
        saved["readerProgress"].append(deepcopy(saved["readerProgress"][0]))
        self.agent = RecordingReader()
        record = await self.run_it(saved_plan=self.plan, saved_record=saved)
        self.assertEqual(record["error"]["code"], "INVALID_READER_CHECKPOINT")
        self.assertEqual(self.agent.calls, [])

    async def test_malformed_checkpoint_flag_not_silently_retried(self):
        saved = await self.run_it(); saved["readerProgress"][0]["succeeded"] = "true"
        self.agent = RecordingReader()
        record = await self.run_it(saved_plan=self.plan, saved_record=saved)
        self.assertEqual(record["error"]["code"], "INVALID_READER_CHECKPOINT")
        self.assertEqual(self.agent.calls, [])

    async def test_changed_search_parent_rejected(self):
        saved = await self.run_it(); saved["outcome"]["result"]["subtaskId"] = "Other"
        self.agent = RecordingReader(); self.search.reset_mock()
        record = await self.run_it(saved_plan=self.plan, saved_record=saved)
        self.assertEqual(record["error"]["code"], "INVALID_SEARCH_CHECKPOINT")
        self.assertEqual(self.agent.calls, []); self.search.assert_not_awaited()

    async def test_missing_pdf_not_fabricated(self):
        self.search.return_value["result"]["items"][1]["data"]["pdfUrl"] = None
        record = await self.run_it()
        self.assertEqual(record["metadata"]["pipelineOutcome"], "partial")
        self.assertIsNone(self.agent.calls[1]["input"]["papers"][0]["pdfUrl"])

    async def test_resume_plan_only_retrieves_without_planner(self):
        record = await self.run_it(saved_plan=self.plan)
        self.planner.assert_not_awaited(); self.search.assert_awaited_once()
        self.assertTrue(record["metadata"]["llmUsed"])


class ReaderEntryTests(unittest.TestCase):
    def test_resume_cli_retains_history_and_skips_planner_search_and_successful_reader(self):
        candidate = chain_plan()
        model = SimpleNamespace(model_validate=lambda data: data)
        async def read(payload, **options):
            return await call_reader_batch(payload, RecordingReader(), model, **options)
        record = asyncio.run(run_pipeline(candidate["userQuery"],
            planner_call=AsyncMock(return_value=(candidate, {})), search_call=AsyncMock(return_value=search_outcome(candidate)), reader_call=read))
        with tempfile.TemporaryDirectory() as directory:
            history = save_pipeline(record, Path(directory) / "history")
            before = (history / "pipeline.json").read_bytes()
            reader = RecordingReader()
            with patch("run_research.ROOT", Path(directory)), patch("run_research.ProviderSettings.from_env") as config, \
                    patch("run_research.prepare_live_wrapper"), patch("run_research.prepare_live_reader", return_value=(reader, model)), \
                    patch("run_research.create_plan", new_callable=AsyncMock) as planner, \
                    patch("run_research.execute_live_task", new_callable=AsyncMock) as search, \
                    patch("builtins.input") as prompt, patch("builtins.print"):
                self.assertEqual(main(["--resume", str(history), "--with-reader", "--reader-root", "unused"]), 0)
                config.assert_not_called(); planner.assert_not_awaited(); search.assert_not_awaited(); prompt.assert_not_called()
            self.assertEqual(reader.calls, [])
            self.assertEqual((history / "pipeline.json").read_bytes(), before)
            self.assertEqual(len(list((Path(directory) / "outputs").glob("research-live-*"))), 1)

    def test_reader_requires_explicit_flag(self):
        with patch("run_research.create_plan") as planner, patch("builtins.print"):
            self.assertEqual(main(["--query", "研究", "--reader-root", "unused"]), 1)
            planner.assert_not_called()

    def test_bad_reader_options_no_configuration_or_planner(self):
        for extra in [[], ["--reader-root", "unused", "--reader-timeout", "nan"]]:
            with patch("run_research.ProviderSettings.from_env") as config, patch("builtins.print"):
                self.assertEqual(main(["--query", "研究", "--with-reader", *extra]), 1)
                config.assert_not_called()

    def test_reader_preflight_failure_no_planner_or_search(self):
        from reader_adapter import ReaderAdapterError
        with patch("run_research.ProviderSettings.from_env"), patch("run_research.prepare_live_wrapper"), \
                patch("run_research.prepare_live_reader", side_effect=ReaderAdapterError("READER_RUNTIME_DEPENDENCIES_MISSING")), \
                patch("run_research.create_plan") as planner, patch("run_research.execute_live_task") as search, \
                patch("builtins.print"):
            self.assertEqual(main(["--query", "研究", "--with-reader", "--reader-root", "unused"]), 1)
            planner.assert_not_called(); search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
