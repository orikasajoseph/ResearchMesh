"""单篇入口安全边界的离线测试；不读取 Key、不请求模型。"""
import asyncio
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import run_reader
from reader_adapter import ReaderAdapterError


class EntryTests(unittest.IsolatedAsyncioTestCase):
    def args(self, **changes):
        values = dict(pdf="unused", wrapper_root="unused", execute=False, timeout=600,
                      runtime_home=None)
        values.update(changes)
        return SimpleNamespace(**values)

    def wrapper(self):
        return SimpleNamespace(PaperReadTask=SimpleNamespace(model_validate=Mock()))

    async def test_default_check_never_prepares_or_calls_provider(self):
        with patch("run_reader.inspect_pdf", return_value=(b"pdf", {"pages": 15})), \
                patch("run_reader.load_reader", return_value=self.wrapper()), \
                patch("run_reader.prepare_live_reader") as prepare, \
                patch("run_reader.call_reader", new_callable=AsyncMock) as read, redirect_stdout(StringIO()):
            self.assertEqual(await run_reader.run(self.args()), 0)
            prepare.assert_not_called()
            read.assert_not_called()

    async def test_local_fetcher_only_returns_supplied_bytes_for_exact_source(self):
        fetch = run_reader.local_pdf_fetcher(b"%PDF-synthetic")
        self.assertEqual(await fetch(run_reader.PDF_URL), b"%PDF-synthetic")
        with self.assertRaisesRegex(ReaderAdapterError, "LOCAL_PDF_SOURCE_MISMATCH"):
            await fetch("https://example.invalid/different.pdf")

    async def test_failure_record_retained_and_no_retry(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("run_reader.ROOT", Path(directory)), \
                patch("run_reader.inspect_pdf", return_value=(b"pdf", {"pages": 15})), \
                patch("run_reader.load_reader", return_value=self.wrapper()), \
                patch("run_reader.prepare_live_reader", return_value=(SimpleNamespace(), "model")) as prepare, \
                patch("run_reader.call_reader", new_callable=AsyncMock,
                    return_value={"result": None, "error": {"code": "READER_TIMEOUT"},
                                  "diagnostics": {"businessCompleted": False}}) as read, \
                redirect_stdout(StringIO()):
            self.assertEqual(await run_reader.run(self.args(execute=True)), 1)
            read.assert_awaited_once()
            self.assertIn("pdf_fetcher", prepare.call_args.kwargs)
            record = json.loads(next(Path(directory).glob("outputs/*/record.json")).read_text(encoding="utf-8"))
            self.assertEqual(record["error"]["code"], "READER_TIMEOUT")
            self.assertFalse(record["metadata"]["aipUsed"])
            self.assertIsNone(record["metadata"]["modelRequestCount"])
            self.assertTrue(record["metadata"]["modelExecutionAttempted"])

    async def test_preparation_failure_does_not_claim_model_execution(self):
        with tempfile.TemporaryDirectory() as directory, patch("run_reader.ROOT", Path(directory)), \
                patch("run_reader.inspect_pdf", return_value=(b"pdf", {"pages": 15})), \
                patch("run_reader.load_reader", return_value=self.wrapper()), \
                patch("run_reader.prepare_live_reader", side_effect=ReaderAdapterError("MISSING_READER_CONFIGURATION")), \
                patch("run_reader.call_reader", new_callable=AsyncMock) as read, redirect_stdout(StringIO()):
            self.assertEqual(await run_reader.run(self.args(execute=True)), 1)
            read.assert_not_called()
            record = json.loads(next(Path(directory).glob("outputs/*/record.json")).read_text(encoding="utf-8"))
            self.assertFalse(record["metadata"]["modelExecutionAttempted"])

    async def test_cancellation_retains_interrupted_record(self):
        with tempfile.TemporaryDirectory() as directory, patch("run_reader.ROOT", Path(directory)), \
                patch("run_reader.inspect_pdf", return_value=(b"pdf", {"pages": 15})), \
                patch("run_reader.load_reader", return_value=self.wrapper()), \
                patch("run_reader.prepare_live_reader", return_value=(SimpleNamespace(), "model")), \
                patch("run_reader.call_reader", new_callable=AsyncMock, side_effect=asyncio.CancelledError), \
                redirect_stdout(StringIO()):
            with self.assertRaises(asyncio.CancelledError):
                await run_reader.run(self.args(execute=True))
            record = json.loads(next(Path(directory).glob("outputs/*/record.json")).read_text(encoding="utf-8"))
            self.assertEqual(record["metadata"]["outcome"], "interrupted")

    async def test_invalid_timeout_rejected_before_pdf_or_model(self):
        with patch("run_reader.inspect_pdf") as inspect:
            for timeout in [0, -1, float("nan"), float("inf")]:
                with self.assertRaisesRegex(ReaderAdapterError, "INVALID_TIMEOUT"):
                    await run_reader.run(self.args(timeout=timeout))
            inspect.assert_not_called()

    def test_task_is_single_paper_business_payload(self):
        task = run_reader.make_task()
        self.assertEqual(task["schemaVersion"], "0.2")
        self.assertEqual(task["constraints"], {"maxPapers": 1})
        self.assertEqual(task["input"]["papers"][0]["pdfUrl"], run_reader.PDF_URL)
        self.assertNotIn("requiredSkill", task)
        self.assertNotIn("dependsOn", task)


if __name__ == "__main__":
    unittest.main()
