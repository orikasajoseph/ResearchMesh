"""Reader input is prepared only after S1 has bound a real PaperRef."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import AsyncMock, patch
import httpx

from aip_reader_inputs import prepare_bound_reader_input
from aip_reader_partner import make_reader_executor, create_live_app
import reader_aip_worker


PDF = "https://arxiv.org/pdf/2305.14314"
TITLE = "QLoRA: Efficient Finetuning of Quantized LLMs"


def task():
    return {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
            "goal": "Read QLoRA", "input": {"papers": [{"paperId": "W1", "title": TITLE,
            "doi": "10.52202/075280-0441", "url": "https://openalex.org/W1",
            "pdfUrl": PDF, "repositoryUrls": ["https://github.com/artidoro/qlora"]}]},
            "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1"}


class DynamicReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_model_request_count_comes_from_physical_audit_starts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "reader_input.json"
            input_path.write_text(json.dumps({"pdf": {"sha256": "a" * 64}}), encoding="utf-8")
            args = SimpleNamespace(
                record_dir=root, bound_task=None, input=input_path, execute=True,
                reader_root=root, env_file=root / "safe.env", runtime_home=None,
                resume_journal=None, group_id="group-audit", session_id="session-audit",
                task_id="task-audit", worker_run_id="run-audit",
            )
            captured = {}
            def prepare(*args, **kwargs):
                captured["audit"] = kwargs["model_http_audit"]
                return (object(), object())
            async def execute(*args, save_started, **kwargs):
                save_started()
                attempt = captured["audit"].started()
                captured["audit"].finished(
                    attempt, response_received=True, http_status=200
                )
                return {"result": {"schemaVersion": "0.2", "subtaskId": "S2",
                    "taskType": "paper_read", "summary": "offline", "items": [],
                    "warnings": []}, "error": None,
                    "diagnostics": {"analysisLevel": "fulltext"}}
            wrapper = SimpleNamespace(PaperReadTask=SimpleNamespace(model_validate=lambda value: value))
            with patch("reader_aip_worker.require_record_directory", return_value=root), \
                 patch("reader_aip_worker.inspect_input", return_value=(task(), b"%PDF-offline")), \
                 patch("reader_aip_worker.load_reader", return_value=wrapper), \
                 patch("reader_aip_worker.check_runtime"), \
                 patch("reader_aip_worker.prepare_live_reader", side_effect=prepare), \
                 patch("reader_aip_worker.execute_one", side_effect=execute):
                self.assertEqual(await reader_aip_worker.run(args), 0)
            journal = json.loads((root / "reader_journal.json").read_text(encoding="utf-8"))
            self.assertEqual(journal["modelRequestCount"], 1)
            self.assertEqual(journal["modelRequestRejectedCount"], 0)
            self.assertTrue(journal["modelRequestAuditComplete"])
            self.assertEqual(journal["modelRequestAuditFailureStages"], [])
            events = [json.loads(line) for line in
                      (root / "model_requests.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len([e for e in events if e["event"] == "MODEL_HTTP_STARTED"]), 1)
            self.assertTrue(all(e["groupId"] == "group-audit" for e in events))
            self.assertTrue(all(e["workerRunId"] == "run-audit" for e in events))

    async def test_worker_does_not_publish_request_count_when_audit_is_incomplete(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "reader_input.json"
            input_path.write_text(json.dumps({"pdf": {"sha256": "a" * 64}}), encoding="utf-8")
            args = SimpleNamespace(
                record_dir=root, bound_task=None, input=input_path, execute=True,
                reader_root=root, env_file=root / "safe.env", runtime_home=None,
                resume_journal=None, group_id="group-audit", session_id="session-audit",
                task_id="task-audit", worker_run_id="run-audit",
            )
            captured = {}
            def prepare(*args, **kwargs):
                captured["audit"] = kwargs["model_http_audit"]
                return (object(), object())
            async def execute(*args, save_started, **kwargs):
                save_started()
                audit = captured["audit"]
                with patch.object(audit, "_write", side_effect=OSError("private audit failure")):
                    audit.started()
                return {"result": {"schemaVersion": "0.2", "subtaskId": "S2",
                    "taskType": "paper_read", "summary": "offline", "items": [],
                    "warnings": []}, "error": None,
                    "diagnostics": {"analysisLevel": "fulltext"}}
            wrapper = SimpleNamespace(PaperReadTask=SimpleNamespace(model_validate=lambda value: value))
            with patch("reader_aip_worker.require_record_directory", return_value=root), \
                 patch("reader_aip_worker.inspect_input", return_value=(task(), b"%PDF-offline")), \
                 patch("reader_aip_worker.load_reader", return_value=wrapper), \
                 patch("reader_aip_worker.check_runtime"), \
                 patch("reader_aip_worker.prepare_live_reader", side_effect=prepare), \
                 patch("reader_aip_worker.execute_one", side_effect=execute):
                self.assertEqual(await reader_aip_worker.run(args), 0)
            journal = json.loads((root / "reader_journal.json").read_text(encoding="utf-8"))
            self.assertIsNone(journal["modelRequestCount"])
            self.assertFalse(journal["modelRequestAuditComplete"])
            self.assertEqual(journal["modelRequestAuditFailureStages"], ["started"])

    async def test_current_task_crosses_business_entry_and_official_aip_without_fixed_input(self):
        from aip_business_partner import create_local_business_app
        from business_execution import execute_business_task
        from run_aip_literature import invoke_business
        from tests.test_aip_reader import journal, note_result
        from reader_adapter import normalize_reader_result
        prepared = []
        async def prepare(current): prepared.append(current)
        async def worker():
            value = journal(task())
            value["outcome"] = normalize_reader_result(task(), note_result(task()))
            return value
        backend = make_reader_executor(worker, prepare_task=prepare)
        async def execute(current):
            return await execute_business_task(current, skill="paper.analyze", backend=backend, allow_execution=True)
        app = create_local_business_app(execute=execute, partner_aic="dynamic-test-reader",
                                       background_execution=True, mode="local-reader-live")
        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("external HTTP")):
            result = await invoke_business(task(), "http://127.0.0.1/rpc", "dynamic-test-reader",
                            transport=httpx.ASGITransport(app=app), expected_task_type="paper_read", poll_seconds=.001)
        self.assertIsNone(result["error"])
        self.assertEqual(result["execution"]["state"], "completed")
        self.assertEqual(prepared, [task()])
        self.assertEqual(result["execution"]["result"]["items"][0]["data"]["source"], task()["input"]["papers"][0])

    async def test_dynamic_preflight_never_downloads_pdf_or_creates_provider(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bound = root / "bound_task.json"; bound.write_text(json.dumps(task()), encoding="utf-8")
            args = SimpleNamespace(record_dir=root, bound_task=bound, input=root / "reader_input.json",
                                   execute=False, reader_root=root)
            with patch("reader_aip_worker.require_record_directory", return_value=root), \
                 patch("reader_aip_worker.check_runtime"), \
                 patch("reader_aip_worker.prepare_live_reader", side_effect=AssertionError("Provider created")), \
                 patch("aip_reader_inputs.prepare_bound_reader_input", side_effect=AssertionError("PDF network")):
                self.assertEqual(await reader_aip_worker.run(args), 0)
            self.assertFalse((root / "reader_input.json").exists())
            preflight = json.loads((root / "reader_preflight.json").read_text(encoding="utf-8"))
            self.assertFalse(preflight["pdfHashAndTitleVerified"])
            self.assertFalse(preflight["pdfDownloadAttempted"])

    async def test_partner_starts_without_fixed_reader_input(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env = {"RESEARCHMESH_AIP_RECORD_DIR": str(root),
                   "RESEARCHMESH_READER_DYNAMIC_INPUT": "1",
                   "RESEARCHMESH_READER_PYTHON": "C:/reader/python.exe",
                   "RESEARCHMESH_READER_ROOT": "C:/reader",
                   "RESEARCHMESH_LOCAL_PARTNER_AIC": "reader-dynamic-offline"}
            with patch("aip_reader_partner.require_record_directory", return_value=root), \
                 patch.dict("aip_reader_partner.os.environ", env):
                app = create_live_app()
            self.assertFalse((root / "reader_input.json").exists())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
                self.assertEqual((await client.get("/health")).status_code, 200)

    async def test_worker_prepares_bound_task_before_reading_input(self):
        from reader_adapter import ReaderAdapterError
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bound = root / "bound_task.json"
            bound.write_text(json.dumps(task()), encoding="utf-8")
            input_path = root / "reader_input.json"
            async def prepare(value, output, *, cache_dir):
                self.assertEqual(value, task())
                input_path.write_text(json.dumps({"payload": value}), encoding="utf-8")
            args = SimpleNamespace(record_dir=root, bound_task=bound, input=input_path, execute=True)
            with patch("reader_aip_worker.require_record_directory", return_value=root), \
                 patch("aip_reader_inputs.prepare_bound_reader_input", side_effect=prepare), \
                 patch("reader_aip_worker.inspect_input", side_effect=ReaderAdapterError("TEST_AFTER_PREPARATION")):
                with self.assertRaisesRegex(ReaderAdapterError, "TEST_AFTER_PREPARATION"):
                    await reader_aip_worker.run(args)
            self.assertTrue(input_path.exists())

    async def test_pdf_preparation_failure_never_creates_provider_or_calls_model(self):
        from reader_adapter import ReaderAdapterError
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bound = root / "bound_task.json"
            bound.write_text(json.dumps(task()), encoding="utf-8")
            input_path = root / "reader_input.json"
            args = SimpleNamespace(record_dir=root, bound_task=bound, input=input_path,
                                   execute=True, reader_root=root)
            provider = patch("reader_aip_worker.prepare_live_reader")
            model_call = patch("reader_aip_worker.execute_one")
            with patch("reader_aip_worker.require_record_directory", return_value=root), \
                 patch("aip_reader_inputs.prepare_bound_reader_input",
                       side_effect=ReaderAdapterError("PDF_DOWNLOAD_FAILED")), \
                 provider as provider_mock, model_call as model_mock:
                with self.assertRaisesRegex(ReaderAdapterError, "PDF_DOWNLOAD_FAILED"):
                    await reader_aip_worker.run(args)
            provider_mock.assert_not_called()
            model_mock.assert_not_called()
            self.assertFalse(input_path.exists())
            self.assertFalse((root / "reader_journal.json").exists())

    async def test_runtime_failure_persists_last_stage_without_marking_running(self):
        from reader_adapter import ReaderAdapterError
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "reader_input.json"
            input_path.write_text(json.dumps({"pdf": {"sha256": "a" * 64}}), encoding="utf-8")
            args = SimpleNamespace(record_dir=root, bound_task=None, input=input_path,
                                   execute=True, reader_root=root, env_file=root / "safe.env",
                                   runtime_home=None, resume_journal=None)
            wrapper = SimpleNamespace(PaperReadTask=SimpleNamespace(model_validate=lambda value: value))
            def fail_runtime(*args, **kwargs):
                audit = kwargs["initialization_audit"]
                audit("prepare_live_reader", "started")
                audit("paperqa_settings_construct", "started")
                audit("paperqa_settings_construct", "failed", "ValueError")
                raise ReaderAdapterError("READER_RUNTIME_PREPARATION_FAILED")
            with patch("reader_aip_worker.require_record_directory", return_value=root), \
                 patch("reader_aip_worker.inspect_input", return_value=(task(), b"%PDF-offline")), \
                 patch("reader_aip_worker.load_reader", return_value=wrapper), \
                 patch("reader_aip_worker.check_runtime"), \
                 patch("reader_aip_worker.prepare_live_reader", side_effect=fail_runtime), \
                 patch("reader_aip_worker.execute_one") as execute:
                self.assertEqual(await reader_aip_worker.run(args), 1)
            execute.assert_not_called()
            journal = json.loads((root / "reader_journal.json").read_text(encoding="utf-8"))
            self.assertEqual(journal["phase"], "failed")
            self.assertFalse(journal["modelExecutionAttemptedThisRun"])
            records = [json.loads(line) for line in
                       (root / "runtime_initialization.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(r["stage"], r["event"]) for r in records], [
                ("prepare_live_reader", "started"),
                ("paperqa_settings_construct", "started"),
                ("paperqa_settings_construct", "failed")])
            self.assertEqual(records[-1]["exceptionClass"], "ValueError")
            self.assertIn("at", records[-1])
            self.assertIsInstance(records[-1]["durationMs"], float)
            self.assertNotIn("safe.env", str(records))

    async def test_bound_pdf_is_verified_and_input_is_written(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            cache.mkdir()
            digest = "a" * 64
            (cache / (digest + ".pdf")).write_bytes(b"%PDF-1.7 offline")
            proof = {"state": "verified", "paperId": "W1", "sha256": digest,
                     "pdfUrl": PDF, "sourceUrl": "https://arxiv.org/abs/2305.14314",
                     "cachePath": str(cache / (digest + ".pdf")), "bytes": 16,
                     "pageTitle": TITLE, "pages": 26, "networkAttempted": True}

            async def resolve(unresolved, source, *, previous=None):
                self.assertIsNone(previous)
                self.assertIsNone(unresolved["input"]["papers"][0]["pdfUrl"])
                self.assertEqual(source["items"][0]["data"]["sourceUrls"],
                                 ["https://arxiv.org/abs/2305.14314"])
                updated = deepcopy(unresolved)
                updated["input"]["papers"][0]["pdfUrl"] = PDF
                return updated, [proof]

            with patch("aip_reader_inputs.ArxivPdfResolver",
                       return_value=SimpleNamespace(resolve=resolve)):
                result = await prepare_bound_reader_input(task(), root, cache_dir=cache)
            self.assertEqual(result["payload"], task())
            self.assertEqual(result["pdf"]["sourceUrl"], proof["sourceUrl"])
            self.assertEqual(json.loads((root / "reader_input.json").read_text(encoding="utf-8")), result)
            reused = root / "second"
            reused.mkdir()
            async def resolve_cached(unresolved, source, *, previous=None):
                self.assertEqual(previous, [proof])
                updated = deepcopy(unresolved)
                updated["input"]["papers"][0]["pdfUrl"] = PDF
                return updated, [{**proof, "networkAttempted": False}]
            with patch("aip_reader_inputs.ArxivPdfResolver",
                       return_value=SimpleNamespace(resolve=resolve_cached)):
                second = await prepare_bound_reader_input(task(), reused, cache_dir=cache)
            self.assertFalse(second["pdfDownloadedThisRun"])
            self.assertNotEqual(root / "reader_input.json", reused / "reader_input.json")

    async def test_arxiv_pdf_suffix_variant_keeps_verified_identity(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            cache.mkdir()
            digest = "b" * 64
            cached = cache / (digest + ".pdf")
            cached.write_bytes(b"%PDF-1.7 offline")
            current = task()
            current["input"]["papers"][0]["pdfUrl"] = PDF + ".pdf"
            proof = {"state": "verified", "paperId": "W1", "sha256": digest,
                     "pdfUrl": PDF, "sourceUrl": "https://arxiv.org/abs/2305.14314",
                     "cachePath": str(cached), "bytes": 16, "pageTitle": TITLE,
                     "pages": 26, "networkAttempted": True}

            async def resolve(unresolved, source, *, previous=None):
                updated = deepcopy(unresolved)
                updated["input"]["papers"][0]["pdfUrl"] = PDF
                return updated, [proof]

            with patch("aip_reader_inputs.ArxivPdfResolver",
                       return_value=SimpleNamespace(resolve=resolve)):
                result = await prepare_bound_reader_input(current, root, cache_dir=cache)
            self.assertEqual(result["payload"]["input"]["papers"][0]["pdfUrl"], PDF + ".pdf")
            self.assertEqual(result["pdf"]["pdfUrl"], PDF)

    async def test_no_pdf_or_mismatched_proof_rejects_before_worker(self):
        from reader_adapter import ReaderAdapterError
        with TemporaryDirectory() as directory:
            invalid = task()
            invalid["input"]["papers"][0]["pdfUrl"] = None
            with self.assertRaises(ReaderAdapterError):
                await prepare_bound_reader_input(invalid, Path(directory), cache_dir=Path(directory))
            worker = AsyncMock()
            prepare = AsyncMock(side_effect=ReaderAdapterError("PDF_SOURCE_UNVERIFIED"))
            execute = make_reader_executor(worker, prepare_task=prepare)
            with self.assertRaises(ReaderAdapterError):
                await execute(task())
            worker.assert_not_awaited()

    async def test_download_failure_and_pdf_identity_mismatch(self):
        from reader_adapter import ReaderAdapterError
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            cache.mkdir()
            async def failed(unresolved, source, *, previous=None):
                return unresolved, [{"state": "unavailable", "reason": "ARXIV_HTTP_503"}]
            with patch("aip_reader_inputs.ArxivPdfResolver",
                       return_value=SimpleNamespace(resolve=failed)):
                with self.assertRaisesRegex(ReaderAdapterError, "PDF_DOWNLOAD_FAILED"):
                    await prepare_bound_reader_input(task(), root, cache_dir=cache)
            wrong = task()
            wrong["input"]["papers"][0]["pdfUrl"] = "https://arxiv.org/pdf/2304.02643"
            async def mismatched(unresolved, source, *, previous=None):
                return unresolved, [{"state": "unavailable", "reason": "SOURCE_TITLE_MISMATCH"}]
            with patch("aip_reader_inputs.ArxivPdfResolver",
                       return_value=SimpleNamespace(resolve=mismatched)):
                with self.assertRaisesRegex(ReaderAdapterError, "PDF_SOURCE_UNVERIFIED"):
                    await prepare_bound_reader_input(wrong, root, cache_dir=cache)

    async def test_completed_result_is_never_reused_for_another_paper(self):
        from aip_business_partner import LocalBackendFailure
        from tests.test_aip_reader import journal, note_result
        original = journal()
        first_task = original["task"]
        second_task = deepcopy(first_task)
        second_task["input"]["papers"][0]["paperId"] = "different-paper"
        prepared = []
        async def prepare(value):
            prepared.append(value)
        async def worker():
            return original
        execute = make_reader_executor(worker, prepare_task=prepare)
        self.assertEqual(await execute(first_task), note_result(first_task))
        with self.assertRaisesRegex(LocalBackendFailure, "READER_PREVIOUS_EXECUTION_UNCERTAIN"):
            await execute(second_task)
        self.assertEqual(prepared, [first_task])
