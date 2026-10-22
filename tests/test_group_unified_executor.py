"""Offline tests for Literature -> Zhao's unified business execution seam."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from aip.literature_unified_executor import (
    execute_unified_literature,
    live_literature_backend,
)
from aip.wu_delivery.contracts import ResearchTask
from business_execution import BusinessExecutionError


def legacy_task():
    return ResearchTask.model_validate({
        "schemaVersion": "0.1",
        "subtaskId": "S1",
        "taskType": "literature_search",
        "goal": "Find a LoRA paper",
        "input": {"query": "LoRA: Low-Rank Adaptation of Large Language Models"},
        "constraints": {"maxResults": 1},
        "outputSchema": "paper_records.v1",
    })


def business_result():
    return {
        "schemaVersion": "0.2",
        "subtaskId": "S1",
        "taskType": "literature_search",
        "summary": "One paper found",
        "items": [{
            "kind": "paper",
            "data": {
                "paperId": "test-lora",
                "title": "LoRA: Low-Rank Adaptation of Large Language Models",
                "doi": None,
                "url": "https://arxiv.org/abs/2106.09685",
                "pdfUrl": "https://arxiv.org/pdf/2106.09685",
                "repositoryUrls": [],
            },
        }],
        "warnings": [],
    }


class UnifiedLiteratureTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalized_contract_once(self):
        backend = AsyncMock(return_value=business_result())
        result = await execute_unified_literature(
            legacy_task(), backend=backend, timeout_seconds=5
        )
        backend.assert_awaited_once()
        payload = backend.await_args.args[0]
        self.assertEqual(len(payload), 7)
        self.assertEqual(payload["schemaVersion"], "0.2")
        self.assertEqual(result.schemaVersion, "0.1")
        self.assertEqual(result.subtaskId, "S1")
        self.assertEqual(len(result.items), 1)

    async def test_bad_task_blocks_backend(self):
        task = legacy_task()
        task.constraints["maxResults"] = 0
        backend = AsyncMock(return_value=business_result())
        with self.assertRaises(BusinessExecutionError) as caught:
            await execute_unified_literature(task, backend=backend)
        self.assertEqual(caught.exception.code, "INVALID_BUSINESS_TASK")
        backend.assert_not_awaited()

    async def test_result_mismatch_is_not_success(self):
        bad = business_result()
        bad["subtaskId"] = "other"
        backend = AsyncMock(return_value=bad)
        with self.assertRaises(BusinessExecutionError) as caught:
            await execute_unified_literature(legacy_task(), backend=backend)
        self.assertEqual(caught.exception.code, "INVALID_BUSINESS_RESULT")
        self.assertTrue(caught.exception.remote_execution_uncertain)

    async def test_timeout_is_uncertain_and_once(self):
        async def timed_out(_payload):
            raise asyncio.TimeoutError()

        backend = AsyncMock(side_effect=timed_out)
        with self.assertRaises(BusinessExecutionError) as caught:
            await execute_unified_literature(legacy_task(), backend=backend)
        self.assertEqual(
            caught.exception.code, "BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED"
        )
        self.assertTrue(caught.exception.remote_execution_uncertain)
        backend.assert_awaited_once()

    async def test_live_backend_disables_recovery_retries(self):
        with patch(
            "aip.literature_unified_executor.execute_live_task",
            new_callable=AsyncMock,
            return_value={"error": None, "result": business_result()},
        ) as real_pipeline:
            result = await live_literature_backend({"schemaVersion": "0.2"})
        self.assertEqual(result["schemaVersion"], "0.2")
        real_pipeline.assert_awaited_once()
        self.assertIs(real_pipeline.await_args.kwargs["retry"], False)


if __name__ == "__main__":
    unittest.main()
