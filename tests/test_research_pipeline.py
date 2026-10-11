"""自然语言串联入口的离线测试：不联网，不消耗 API 额度。"""

from copy import deepcopy
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from literature_adapter import AdapterError, normalize_response, to_wrapper_request
from run_literature import execute_live_task, prepare_live_wrapper, select_task
from run_research import load_resume_plan, main, render_pipeline_report, run_pipeline, save_pipeline
from search_recovery import recover_rate_limit
from tests.test_literature_adapter import plan, response


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plan = plan()
        self.query = self.plan["userQuery"]
        self.planner = AsyncMock(return_value=(self.plan, {"requestCount": 1, "planValidated": True}))
        payload = select_task(self.plan)
        self.outcome = normalize_response(payload, to_wrapper_request(payload, "test-request"), response())
        self.search = AsyncMock(return_value=self.outcome)

    async def run_it(self):
        return await run_pipeline(self.query, planner_call=self.planner, search_call=self.search)

    async def test_plan_to_search_and_no_aip(self):
        before = deepcopy(self.plan)
        record = await self.run_it()
        self.assertEqual(record["metadata"]["pipelineOutcome"], "completed")
        self.assertTrue(record["metadata"]["llmUsed"])
        self.assertTrue(record["metadata"]["agentsExecuted"])
        self.assertFalse(record["metadata"]["aipUsed"])
        self.assertFalse(record["metadata"]["platformDiscoveryUsed"])
        self.planner.assert_awaited_once_with(self.query)
        self.search.assert_awaited_once_with(select_task(self.plan))
        self.assertEqual(self.plan, before)
        # 历史 Planner 元数据表示该阶段未执行 Agent；总链路单独正确记录。
        self.assertEqual(record["plannerMetadata"]["requestCount"], 1)

    async def test_clarification_does_not_search(self):
        self.plan.update(needsClarification=True, clarificationQuestion="请补充主题", tasks=[])
        record = await self.run_it()
        self.assertEqual(record["metadata"]["pipelineOutcome"], "needs-clarification")
        self.search.assert_not_awaited()
        self.assertFalse(record["metadata"]["agentsExecuted"])
        self.assertIn("请补充主题", render_pipeline_report(record))

    async def test_complex_plan_not_partially_executed(self):
        task = deepcopy(self.plan["tasks"][0]); task["subtaskId"] = "S2"
        self.plan["tasks"].append(task)
        record = await self.run_it()
        self.search.assert_not_awaited()
        self.assertEqual(record["error"]["code"], "ONLY_STANDALONE_LITERATURE_PLAN_SUPPORTED")
        self.assertEqual(record["plan"], self.plan)

    async def test_paper_read_not_executed(self):
        self.plan["tasks"][0].update(taskType="paper_read", requiredSkill="paper.analyze",
                                    outputSchema="paper_notes.v1", constraints={"maxPapers": 1},
                                    input={"papers": [{"url": "https://arxiv.org/abs/1706.03762"}]})
        record = await self.run_it()
        self.search.assert_not_awaited()
        self.assertEqual(record["error"]["code"], "ONLY_STANDALONE_LITERATURE_PLAN_SUPPORTED")

    async def test_unknown_constraint_not_discarded(self):
        self.plan["tasks"][0]["constraints"]["openAccessOnly"] = True
        record = await self.run_it()
        self.search.assert_not_awaited()
        self.assertEqual(record["error"]["code"], "UNSUPPORTED_CONSTRAINT")

    async def test_blank_query_no_api(self):
        self.query = " "
        record = await self.run_it()
        self.planner.assert_not_awaited(); self.search.assert_not_awaited()
        self.assertFalse(record["metadata"]["llmUsed"])
        self.assertEqual(record["error"]["code"], "INVALID_QUERY")

    async def test_query_mismatch_no_search(self):
        self.query = "另一个研究问题"
        record = await self.run_it()
        self.search.assert_not_awaited()
        self.assertEqual(record["error"]["code"], "PLAN_QUERY_MISMATCH")

    async def test_planning_failure_redacted(self):
        self.planner.side_effect = RuntimeError("secret-test-key")
        record = await self.run_it()
        self.search.assert_not_awaited()
        self.assertNotIn("secret-test-key", json.dumps(record))
        self.assertEqual(record["metadata"]["pipelineOutcome"], "failed")

    async def test_retrieval_error_retains_plan_and_no_fallback(self):
        self.search.return_value = {"result": None, "error": {"code": "RATE_LIMITED", "retryable": True},
                                    "diagnostics": {}}
        record = await self.run_it()
        self.assertEqual(record["metadata"]["pipelineOutcome"], "retrieval-failed")
        self.assertEqual(record["plan"], self.plan)
        self.assertIsNone(record["outcome"]["result"])
        self.search.assert_awaited_once()

    async def test_timeout_redacted(self):
        self.search.side_effect = AdapterError("WRAPPER_TIMEOUT")
        record = await self.run_it()
        self.assertEqual(record["error"]["code"], "WRAPPER_TIMEOUT")
        self.assertIn("WRAPPER_TIMEOUT", render_pipeline_report(record))

    async def test_empty_results_not_invented(self):
        self.outcome["result"].update(items=[], summary="未返回匹配论文")
        record = await self.run_it()
        self.assertEqual(record["metadata"]["pipelineOutcome"], "empty")
        self.assertIsNone(record["error"])
        self.assertEqual(record["outcome"]["result"]["items"], [])

    async def test_report_truthful_and_save_all_artifacts(self):
        record = await self.run_it()
        with tempfile.TemporaryDirectory() as directory:
            first = save_pipeline(record, directory)
            second = save_pipeline(record, directory)
            self.assertNotEqual(first, second)
            names = {p.name for p in first.iterdir()}
            self.assertEqual(names, {"plan.json", "planner_metadata.json", "outcome.json", "metadata.json",
                                     "pipeline.json", "reader_inputs.json", "report.md"})
            text = (first / "report.md").read_text(encoding="utf-8")
            self.assertIn("真实 LLM", text)
            self.assertNotIn("未调用 LLM", text)
            self.assertIn("未调用 ADP 或 AIP", text)
            refs = json.loads((first / "reader_inputs.json").read_text(encoding="utf-8"))
            self.assertEqual(refs["papers"][0]["doi"], "10.1234/test")

    async def test_failure_save_has_no_reader_inputs(self):
        self.search.side_effect = AdapterError("WRAPPER_TIMEOUT")
        record = await self.run_it()
        with tempfile.TemporaryDirectory() as directory:
            output = save_pipeline(record, directory)
            self.assertTrue((output / "plan.json").is_file())
            self.assertFalse((output / "reader_inputs.json").exists())

    async def test_live_helper_checks_payload_before_provider(self):
        payload = select_task(self.plan)
        payload["constraints"]["openAccessOnly"] = True
        with patch("run_literature.prepare_live_wrapper") as prepare:
            with self.assertRaisesRegex(AdapterError, "UNSUPPORTED_CONSTRAINT"):
                await execute_live_task(payload, "not-used")
            prepare.assert_not_called()

    async def test_429_recovery_does_not_repeat_planner(self):
        limited = {"result": None, "error": {"code": "RATE_LIMITED", "retryable": True}, "diagnostics": {}}
        attempts, sleep = AsyncMock(side_effect=[limited, self.outcome]), AsyncMock()

        async def search(payload):
            return await recover_rate_limit(attempts, sleep=sleep)

        record = await run_pipeline(self.query, planner_call=self.planner, search_call=search)
        self.planner.assert_awaited_once()
        self.assertEqual(attempts.await_count, 2)
        self.assertEqual(record["metadata"]["pipelineOutcome"], "completed")

    async def test_live_helper_reuses_provider_and_request_id(self):
        ids = []

        class Request:
            @staticmethod
            def model_validate(data):
                return SimpleNamespace(**data)

        class Agent:
            async def literature_search(self, request):
                ids.append(request.requestId)
                raw = response(); raw["requestId"] = request.requestId
                if len(ids) == 1:
                    raw.update(status="failure", papers=[], total=0,
                               error={"code": "RATE_LIMITED", "message": "limited", "retryable": True})
                return SimpleNamespace(model_dump=lambda **kw: raw)

        from unittest.mock import Mock
        wrapper = SimpleNamespace(SemanticScholarProvider=Mock(), LiteratureAgent=Mock(return_value=Agent()),
                                  LiteratureSearchRequest=Request)
        sleeper = AsyncMock()

        async def recover(call, *, progress, delays):
            return await recover_rate_limit(call, sleep=sleeper, progress=progress,
                                            delays=delays, jitter=lambda: 0)

        with patch("run_literature.recover_rate_limit", side_effect=recover):
            result = await execute_live_task(select_task(self.plan), "unused",
                                             prepared=(wrapper, None), progress=None)
        self.assertIsNone(result["error"])
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids[0], ids[1])
        wrapper.SemanticScholarProvider.assert_called_once()
        wrapper.LiteratureAgent.assert_called_once()
        sleeper.assert_awaited_once_with(2)

    async def test_resume_never_calls_planner(self):
        record = await run_pipeline(self.query, planner_call=self.planner, search_call=self.search,
                                    saved_plan=self.plan)
        self.planner.assert_not_awaited()
        self.assertFalse(record["metadata"]["llmUsed"])
        self.assertTrue(record["metadata"]["resumedPlan"])
        self.assertIsNone(record["plannerMetadata"])
        self.assertIn("未调用 LLM", render_pipeline_report(record))

    async def test_checkpoint_written_before_search(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []

            def checkpoint(record):
                self.assertFalse(record["metadata"]["agentsExecuted"])
                paths.append(save_pipeline(record, directory))

            async def search(payload):
                self.assertTrue((paths[0] / "plan.json").is_file())
                return self.outcome

            record = await run_pipeline(self.query, planner_call=self.planner, search_call=search,
                                        on_plan=checkpoint)
            output = save_pipeline(record, output_dir=paths[0])
            self.assertEqual(output, paths[0])
            self.assertEqual(json.loads((output / "metadata.json").read_text())["pipelineOutcome"], "completed")

    async def test_cancellation_preserves_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []

            def checkpoint(record):
                paths.append(save_pipeline(record, directory))

            self.search.side_effect = asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await run_pipeline(self.query, planner_call=self.planner, search_call=self.search,
                                   on_plan=checkpoint)
            self.assertEqual(load_resume_plan(paths[0]), self.plan)
            self.assertNotIn("保留 1", (paths[0] / "report.md").read_text(encoding="utf-8"))


class EntryTests(unittest.TestCase):
    def test_missing_semantic_key_no_wrapper_load(self):
        with patch.dict("os.environ", {}, clear=True), patch("dotenv.dotenv_values", return_value={}), \
                patch("run_literature.load_wrapper") as load:
            with self.assertRaisesRegex(AdapterError, "MISSING_SEMANTIC_SCHOLAR_API_KEY"):
                prepare_live_wrapper("not-used")
            load.assert_not_called()

    def test_preflight_failure_does_not_call_planner(self):
        with patch("run_research.ProviderSettings.from_env"), \
                patch("run_research.prepare_live_wrapper", side_effect=AdapterError("MISSING_SEMANTIC_SCHOLAR_API_KEY")), \
                patch("run_research.create_plan") as create, patch("builtins.print"):
            self.assertEqual(main(["--query", "检索相关论文"]), 1)
            create.assert_not_called()

    def test_blank_cli_no_configuration_or_api(self):
        with patch("run_research.ProviderSettings.from_env") as settings, patch("builtins.print"):
            self.assertEqual(main(["--query", " "]), 1)
            settings.assert_not_called()

    def test_resume_cli_no_prompt_no_llm_config_no_history_overwrite(self):
        candidate = plan()
        payload = select_task(candidate)
        outcome = normalize_response(payload, to_wrapper_request(payload, "test-request"), response())
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "old"; history.mkdir()
            source = history / "plan.json"
            source.write_text(json.dumps(candidate), encoding="utf-8")
            before = source.read_bytes()
            with patch("run_research.ROOT", Path(directory)), \
                    patch("run_research.ProviderSettings.from_env") as config, \
                    patch("run_research.prepare_live_wrapper", return_value=(None, None)), \
                    patch("run_research.create_plan", new_callable=AsyncMock) as planner, \
                    patch("run_research.execute_live_task", new_callable=AsyncMock, return_value=outcome), \
                    patch("builtins.input") as prompt, patch("builtins.print"):
                self.assertEqual(main(["--resume", str(history)]), 0)
                config.assert_not_called(); planner.assert_not_awaited(); prompt.assert_not_called()
            self.assertEqual(source.read_bytes(), before)

    def test_invalid_resume_no_api(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("run_research.prepare_live_wrapper") as prepare, \
                patch("run_research.create_plan") as planner, patch("builtins.print"):
            self.assertEqual(main(["--resume", str(Path(directory) / "missing.json")]), 1)
            prepare.assert_not_called(); planner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
