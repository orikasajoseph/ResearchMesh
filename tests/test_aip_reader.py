"""No keys, paid models, external PDFs or external network in these tests."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from aip_business_partner import create_local_business_app, LocalBackendFailure
from aip_reader_inputs import prepare_reader_input
from aip_reader_partner import make_reader_executor
import local_process
from reader_adapter import ReaderAdapterError, normalize_reader_result
import reader_aip_worker as worker
import run_aip_reader as reader_cli
from run_aip_business import fixture_plan
from run_aip_literature import invoke_business, select_search_task
from run_aip_reader import assess_reader_run
from verify_reader_aip import fixture_task


def note_result(task=None):
    task = task or fixture_task()
    paper = deepcopy(task["input"]["papers"][0])
    return {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read", "summary": "Synthetic note",
            "items": [{"kind": "paper_note", "data": {"source": paper, "method": "Synthetic method",
                "findings": "Synthetic finding", "limitations": "Synthetic limitation", "analysisLevel": "fulltext",
                "evidence": [{"sourceId": paper["paperId"], "url": paper["pdfUrl"], "locator": "Synthetic page 2",
                              "supports": "Synthetic evidence supports this specific conclusion."}]}}], "warnings": []}


def journal(task=None):
    task = task or fixture_task()
    return {"scope": "LIVE_READER_LOCAL_PDF_WORKER", "phase": "completed", "task": deepcopy(task),
            "pdf": {"sha256": "a" * 64}, "modelExecutionAttemptedEver": True,
            "modelExecutionAttemptedThisRun": True,
            "outcome": normalize_reader_result(task, note_result(task))}


class ReaderAipTests(unittest.IsolatedAsyncioTestCase):
    async def run_app(self, execute, task=None):
        app = create_local_business_app(execute=execute, partner_aic="reader-test",
                                       background_execution=True, mode="local-reader-live")
        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("external network")):
            return await invoke_business(task or fixture_task(), "http://127.0.0.1/rpc", "reader-test",
                       expected_task_type="paper_read", transport=httpx.ASGITransport(app=app), poll_seconds=.001)

    async def test_single_worker_official_aip_roundtrip(self):
        called = []
        async def backend():
            called.append(True)
            return journal()
        out = await self.run_app(make_reader_executor(backend, fixture_task()))
        self.assertIsNone(out["error"])
        self.assertEqual(called, [True])
        self.assertEqual(out["execution"]["result"], note_result())
        self.assertTrue(assess_reader_run(out, journal(), fixture_task())["businessReady"])
        cmds = [row["command"]["command"] for row in out["aipMessages"]]
        self.assertEqual(cmds[0], "start"); self.assertIn("get", cmds); self.assertEqual(cmds[-1], "complete")

    async def test_repeat_new_task_reuses_cached_result(self):
        calls = []
        async def backend(): calls.append(True); return journal()
        execute = make_reader_executor(backend, fixture_task())
        first, second = await self.run_app(execute), await self.run_app(execute)
        self.assertEqual(calls, [True])
        self.assertNotEqual(first["execution"]["taskId"], second["execution"]["taskId"])
        self.assertEqual(first["execution"]["result"], second["execution"]["result"])

    async def test_concurrent_start_does_not_repeat_worker(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def backend():
            calls.append(True); entered.set(); await release.wait(); return journal()
        execute = make_reader_executor(backend, fixture_task())
        first = asyncio.create_task(execute(fixture_task()))
        await entered.wait()
        try:
            with self.assertRaisesRegex(LocalBackendFailure, "READER_PREVIOUS_EXECUTION_UNCERTAIN"):
                await execute(fixture_task())
        finally:
            release.set(); await first
        self.assertEqual(calls, [True])

    async def test_failed_worker_not_automatically_restarted(self):
        calls = []
        async def backend():
            calls.append(True)
            value = journal(); value.update(phase="failed", outcome={"result": None, "error": {"code": "READER_TIMEOUT"}})
            return value
        execute = make_reader_executor(backend, fixture_task())
        out = await self.run_app(execute)
        self.assertEqual(out["error"]["code"], "PARTNER_FAILED")
        with self.assertRaisesRegex(LocalBackendFailure, "READER_PREVIOUS_EXECUTION_UNCERTAIN"):
            await execute(fixture_task())
        self.assertEqual(calls, [True])

    async def test_changed_task_rejected_without_worker(self):
        called = []
        async def backend(): called.append(True); return journal()
        changed = fixture_task(); changed["goal"] = "Changed goal"
        out = await self.run_app(make_reader_executor(backend, fixture_task()), changed)
        self.assertEqual(out["error"]["code"], "PARTNER_FAILED"); self.assertFalse(called)

    async def test_empty_abstract_mock_wrong_evidence_rejected(self):
        for kind in ("empty", "abstract", "mock", "wrong-source", "error-with-result"):
            value = journal()
            raw = value["outcome"]["result"]
            if kind == "empty": raw["items"] = []
            elif kind == "abstract": raw["items"][0]["data"]["analysisLevel"] = "abstract"
            elif kind == "mock": raw["items"][0]["data"]["isMock"] = True
            elif kind == "wrong-source": raw["items"][0]["data"]["evidence"][0]["sourceId"] = "other"
            else: value["outcome"]["error"] = {"code": "READER_TIMEOUT"}
            async def backend(): return value
            with self.subTest(kind=kind):
                out = await self.run_app(make_reader_executor(backend, fixture_task()))
                self.assertIsNotNone(out["error"]); self.assertIsNone(out["execution"])

    async def test_worker_exception_does_not_leak_details(self):
        async def backend(): raise RuntimeError("secret-provider-configuration")
        out = await self.run_app(make_reader_executor(backend, fixture_task()))
        self.assertNotIn("secret-provider", json.dumps(out))
        self.assertEqual(out["error"]["code"], "PARTNER_FAILED")

    async def test_health_reader_skill_no_research_claim(self):
        app = create_local_business_app(execute=Mock(), partner_aic="reader-test", mode="local-reader-live",
                                       background_execution=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            data = (await client.get("/health")).json()
        self.assertEqual(data["skills"], ["paper.analyze"]); self.assertNotIn("businessReady", data)


class ReaderCheckpointTests(unittest.TestCase):
    def test_completed_checkpoint_reused_without_provider(self):
        with patch.object(worker, "prepare_live_reader", side_effect=AssertionError("LLM provider created")):
            value = worker.reuse_completed(journal(), fixture_task(), {"sha256": "a" * 64})
        self.assertEqual(value["result"], note_result())

    def test_running_failed_interrupted_not_resumed(self):
        for phase in ("running", "failed", "interrupted", "preparing"):
            value = journal(); value["phase"] = phase
            with self.subTest(phase=phase), self.assertRaisesRegex(ReaderAdapterError, "READER_PREVIOUS_EXECUTION_UNCERTAIN"):
                worker.reuse_completed(value, fixture_task(), {"sha256": "a" * 64})

    def test_changed_task_pdf_scope_model_or_error_not_reused(self):
        for key in ("task", "pdf", "scope", "model", "error"):
            value = journal()
            if key == "task": value["task"]["goal"] = "Changed"
            elif key == "pdf": value["pdf"]["sha256"] = "b" * 64
            elif key == "scope": value["scope"] = "OFFLINE_FAKE_PROVIDER"
            elif key == "model": value["modelExecutionAttemptedEver"] = False
            else: value["outcome"]["error"] = {"code": "READER_TIMEOUT"}
            with self.subTest(key=key), self.assertRaises(ReaderAdapterError):
                worker.reuse_completed(value, fixture_task(), {"sha256": "a" * 64})

    def test_live_assessment_rejects_mismatched_result(self):
        value = journal(); out = {"execution": {"state": "completed", "result": note_result(), "simulatedResult": False}, "error": None}
        self.assertTrue(assess_reader_run(out, value, fixture_task())["passed"])
        out["execution"]["result"]["summary"] = "Modified"
        self.assertFalse(assess_reader_run(out, value, fixture_task())["passed"])

    def test_fake_worker_journal_not_live_success(self):
        value = journal(); value["scope"] = "OFFLINE_FAKE_PROVIDER"
        out = {"execution": {"state": "completed", "result": note_result(), "simulatedResult": False}, "error": None}
        self.assertFalse(assess_reader_run(out, value, fixture_task())["businessReady"])

    def test_worker_checks_cache_hash_and_title_before_provider(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); cache = root / "outputs/pdf-cache"; cache.mkdir(parents=True)
            content = b"%PDF-SYNTHETIC-UNIT-TEST"; digest = hashlib.sha256(content).hexdigest()
            path = cache / (digest + ".pdf"); path.write_bytes(content)
            data = {"payload": fixture_task(), "pdf": {"sha256": digest, "path": str(path), "bytes": len(content),
                    "pdfUrl": fixture_task()["input"]["papers"][0]["pdfUrl"], "pages": 1}}
            with patch.object(worker, "ROOT", root), patch.object(worker, "inspect_pdf", return_value=1) as inspect:
                self.assertEqual(worker.inspect_input(data)[1], content)
                inspect.assert_called_once_with(content, "Offline fixture paper")
                path.write_bytes(b"%PDF-TAMPERED")
                with self.assertRaisesRegex(ReaderAdapterError, "READER_PDF_CACHE_INVALID"): worker.inspect_input(data)

    def test_worker_rejects_internal_planner_fields(self):
        data = {"payload": fixture_task()}; data["payload"].update(requiredSkill="paper.analyze", dependsOn=[])
        with self.assertRaisesRegex(ReaderAdapterError, "INVALID_READER_TASK"): worker.inspect_input(data)

    def test_default_cli_never_launches_partner_or_execute_worker(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); python = root / "python.exe"; python.touch()
            with patch.object(reader_cli, "ROOT", root), patch.object(reader_cli, "prepare_reader_input",
                    return_value={"payload": fixture_task()}), patch.object(reader_cli.subprocess, "run",
                    return_value=SimpleNamespace(returncode=0)) as run, patch.object(reader_cli, "live_partner",
                    side_effect=AssertionError("Partner started without --execute")):
                code = reader_cli.main(["--source", str(root), "--pdf-proof", str(root),
                            "--reader-root", str(root), "--reader-python", str(python)])
            self.assertEqual(code, 0)
            self.assertNotIn("--execute", run.call_args.args[0])

    def test_completed_worker_resume_writes_new_journal_without_provider(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); output = root / "outputs/new-run"; output.mkdir(parents=True)
            old = root / "previous-journal.json"; old.write_text(json.dumps(journal()), encoding="utf-8")
            before = old.read_bytes()
            input_path = root / "input.json"
            input_path.write_text(json.dumps({"payload": fixture_task(), "pdf": {"sha256": "a" * 64}}))
            args = SimpleNamespace(record_dir=str(output), input=str(input_path), reader_root=str(root),
                                   resume_journal=str(old), execute=True)
            fake_wrapper = SimpleNamespace(PaperReadTask=SimpleNamespace(model_validate=lambda task: task))
            with patch.object(worker, "ROOT", root), patch.object(worker, "inspect_input", return_value=(fixture_task(), b"cached")), \
                 patch.object(worker, "load_reader", return_value=fake_wrapper), patch.object(worker, "prepare_live_reader",
                    side_effect=AssertionError("Reader provider created during completed resume")):
                self.assertEqual(asyncio.run(worker.run(args)), 0)
            value = json.loads((output / "reader_journal.json").read_text())
            self.assertTrue(value["readerResultReused"]); self.assertFalse(value["modelExecutionAttemptedThisRun"])
            self.assertTrue(value["modelExecutionAttemptedEver"]); self.assertEqual(old.read_bytes(), before)


class ReaderInputTests(unittest.TestCase):
    def make_files(self, directory):
        root = Path(directory); source = root / "source"; source.mkdir(); cache = root / "cache"; cache.mkdir()
        plan = fixture_plan(); payload = select_search_task(plan)
        paper = {"paperId": "p1", "title": "Synthetic paper", "doi": None, "url": "https://arxiv.org/abs/1711.02132",
                 "pdfUrl": None, "repositoryUrls": []}
        result = {"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search", "summary": "Synthetic",
                  "items": [{"kind": "paper", "data": dict(paper, year=2017, authors=["Synthetic"], relevance="Synthetic")}], "warnings": []}
        messages = []
        for command, state in (("start", "working"), ("get", "awaiting-completion"), ("complete", "completed")):
            request = {"command": command, "taskId": "aip-search-task", "sessionId": "aip-session"}
            if command == "start": request["dataItems"] = [{"type": "data", "data": payload}]
            response = {"result": {"taskId": "aip-search-task", "sessionId": "aip-session", "senderId": "search-agent",
                                  "senderRole": "partner", "status": {"state": state}}}
            messages.append({"command": request, "response": response})
        outcome = {"execution": {"state": "completed", "result": result, "simulatedResult": False,
                   "taskId": "aip-search-task", "subtaskId": "S1", "agentAic": "search-agent"}, "error": None,
                   "aipMessages": messages}
        backend = {"result": deepcopy(result), "error": None, "diagnostics": {"httpObservationAvailable": True,
                   "httpRequests": [{"httpStatus": 200}]}}
        for name, value in (("plan", plan), ("payload", payload), ("outcome", outcome), ("backend_outcome", backend)):
            (source / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
        content = b"%PDF-SYNTHETIC"; digest = hashlib.sha256(content).hexdigest(); (cache / (digest + ".pdf")).write_bytes(content)
        proof = [{"state": "verified", "paperBefore": paper, "sourceUrl": paper["url"],
                  "pdfUrl": "https://arxiv.org/pdf/1711.02132", "sha256": digest, "bytes": len(content),
                  "pages": 1, "pageTitle": "Synthetic paper"}]
        proof_path = root / "proof.json"; proof_path.write_text(json.dumps(proof), encoding="utf-8")
        return source, proof_path, cache

    def test_binds_real_shaped_search_single_paper_without_mutation(self):
        with TemporaryDirectory() as directory:
            source, proof, cache = self.make_files(directory)
            before = {p.name: p.read_bytes() for p in source.glob("*.json")}
            with patch.object(httpx.Client, "get", side_effect=AssertionError("network")):
                data = prepare_reader_input(source, proof, cache_dir=cache)
            self.assertEqual(len(data["payload"]), 7)
            self.assertEqual(data["payload"]["input"]["papers"][0]["pdfUrl"], "https://arxiv.org/pdf/1711.02132")
            self.assertTrue(data["searchReused"]); self.assertFalse(data["pdfDownloadedThisRun"])
            self.assertEqual(before, {p.name: p.read_bytes() for p in source.glob("*.json")})

    def test_invalid_protocol_or_session_rejected(self):
        for kind in ("completed", "session", "start-payload", "fake"):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                source, proof, cache = self.make_files(directory)
                path = source / "outcome.json"; out = json.loads(path.read_text())
                if kind == "completed": out["aipMessages"][-1]["response"]["result"]["status"]["state"] = "working"
                elif kind == "session": out["aipMessages"][1]["command"]["sessionId"] = "other"
                elif kind == "start-payload": out["aipMessages"][0]["command"]["dataItems"][0]["data"]["goal"] = "Changed"
                else: out["execution"]["simulatedResult"] = True
                path.write_text(json.dumps(out))
                with self.assertRaisesRegex(ReaderAdapterError, "AIP_SEARCH_CHECKPOINT_INVALID"):
                    prepare_reader_input(source, proof, cache_dir=cache)

    def test_unverified_different_identity_hash_and_missing_cache_rejected(self):
        for kind in ("unverified", "arxiv", "hash", "missing"):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                source, proof, cache = self.make_files(directory); data = json.loads(proof.read_text())
                if kind == "unverified": data[0]["state"] = "unverified"
                elif kind == "arxiv": data[0]["sourceUrl"] = "https://arxiv.org/abs/1706.03762"
                elif kind == "hash": next(cache.glob("*.pdf")).write_bytes(b"%PDF-TAMPERED")
                else: data[0]["sha256"] = "b" * 64
                proof.write_text(json.dumps(data))
                with self.assertRaisesRegex(ReaderAdapterError, "VERIFIED_PDF_CACHE_UNAVAILABLE"):
                    prepare_reader_input(source, proof, cache_dir=cache)


class OwnedProcessTests(unittest.TestCase):
    def test_exited_child_is_not_killed(self):
        process = Mock(); process.poll.return_value = 0
        with patch.object(local_process.subprocess, "run") as kill:
            local_process.stop_owned_process(process)
        kill.assert_not_called(); process.terminate.assert_not_called()

    def test_windows_targets_only_owned_pid_and_descendants(self):
        process = Mock(pid=12345); process.poll.return_value = None
        with patch.object(local_process.os, "name", "nt"), patch.object(local_process.subprocess, "run",
                return_value=SimpleNamespace(returncode=0)) as kill:
            local_process.stop_owned_process(process)
        self.assertEqual(kill.call_args.args[0], ["taskkill", "/PID", "12345", "/T", "/F"])
        self.assertEqual(kill.call_args.kwargs["creationflags"], local_process.subprocess.CREATE_NO_WINDOW)
        process.wait.assert_called_once_with(timeout=5)

    def test_failed_tree_cleanup_does_not_claim_stopped(self):
        process = Mock(pid=12345); process.poll.return_value = None
        with patch.object(local_process.os, "name", "nt"), patch.object(local_process.subprocess, "run",
                return_value=SimpleNamespace(returncode=1)), self.assertRaisesRegex(RuntimeError, "STOP_NOT_VERIFIED"):
            local_process.stop_owned_process(process)
