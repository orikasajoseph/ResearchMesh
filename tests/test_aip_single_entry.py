"""Offline orchestration tests. Commands/providers are replaced; no paid calls."""
import asyncio
from copy import deepcopy
import json
import io
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_aip_research as pipeline
import aip_pdf_worker
from export_team_evidence import sanitized_evidence
import tests.test_aip_evidence_assembly as assembly_fixture


class PipelineTests(unittest.TestCase):
    def test_relay_only_generated_retry_lines_not_raw_logs_or_secrets(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); child = root / "literature"; child.mkdir()
            line = "检索网络暂时失败（CONNECT_TIMEOUT），等待 1.2 秒后重试（1/2）；只重试检索，不再调用模型。"
            (child / "partner.log").write_text(line + "\nSECRET_SENTINEL\nraw exception: private\n", encoding="utf-8")
            output = io.StringIO(); seen = set()
            with redirect_stdout(output):
                pipeline.show_search_progress(root / "literature.log", seen)
                pipeline.show_search_progress(root / "literature.log", seen)
            self.assertEqual(output.getvalue().strip(), line)

    def test_actual_offline_subprocess_output_is_polled_and_returns_exit_code(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "literature.log"
            code = pipeline.run_command([sys.executable, "-c", "print('offline subprocess only')"], output, 5)
            self.assertEqual(code, 0)
            self.assertIn("offline subprocess only", output.read_text(encoding="utf-8"))

    def test_failure_details_only_return_whitelisted_codes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); child = root / "literature"; child.mkdir()
            path = child / "backend_outcome.json"
            data = {"error": {"code": "PROVIDER_UNAVAILABLE", "message": "SECRET_SENTINEL"},
                    "diagnostics": {"httpRequests": [{"httpStatus": None, "transportError": "CONNECT_TIMEOUT"}]}}
            path.write_text(json.dumps(data), encoding="utf-8")
            details = pipeline.search_failure_details(root)
            self.assertEqual(details, {"providerError": "PROVIDER_UNAVAILABLE", "transportError": "CONNECT_TIMEOUT"})
            self.assertNotIn("SECRET_SENTINEL", json.dumps(details))
            data["error"]["code"] = "SECRET_SENTINEL"; path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(pipeline.search_failure_details(root), {})

    def setup_files(self, directory):
        source, reader, cache = assembly_fixture.AssemblyTests().files(directory)
        root = Path(directory) / "pipeline"; root.mkdir()
        plan = json.loads((source / "plan.json").read_text())
        (root / "plan.json").write_text(json.dumps(plan))
        state = {"scope": "LOCAL_AIP_SINGLE_ENTRY_V1", "stages": {}, "query": None}
        pipeline.persist(root, state)
        args = SimpleNamespace(wrapper_root=Path(directory), reader_root=Path(directory),
            reader_python=Path("fake-reader-python"), pdf_proof=Path("fake-proof"), llm_adapter="existing-async")
        return root, state, args, plan

    def test_scope_rejects_repository_multi_paper_or_reversed_dependencies(self):
        with TemporaryDirectory() as directory:
            _, _, _, plan = self.setup_files(directory)
            self.assertEqual(pipeline.pair(plan), ("S1", "S2"))
            for change in ("multi", "extra", "independent", "provided"):
                changed = deepcopy(plan)
                if change == "multi": changed["tasks"][1]["constraints"]["maxPapers"] = 2
                elif change == "extra": changed["tasks"].append(deepcopy(changed["tasks"][1]))
                elif change == "independent": changed["tasks"][1]["dependsOn"] = []
                else: changed["tasks"][1]["input"]["papers"] = [{"url": "https://example.invalid/paper"}]
                with self.subTest(change=change), self.assertRaises(ValueError): pipeline.pair(changed)

    def test_completed_stage_skipped_failed_and_running_never_replayed(self):
        with TemporaryDirectory() as directory:
            root, state, _, _ = self.setup_files(directory)
            calls = []
            runner = lambda *a: calls.append(a) or 0
            pipeline.stage(root, state, "reader", ["fake"], timeout=1, runner=runner)
            pipeline.stage(root, state, "reader", ["fake"], timeout=1, runner=runner)
            self.assertEqual(len(calls), 1)
            for status in ("running", "failed"):
                state["stages"]["reader"]["state"] = status
                with self.assertRaises(ValueError): pipeline.stage(root, state, "reader", ["fake"], timeout=1, runner=runner)
            self.assertEqual(len(calls), 1)

    def test_failure_stored_before_stop_and_uncertain_call_stays_running(self):
        with TemporaryDirectory() as directory:
            root, state, _, _ = self.setup_files(directory)
            with self.assertRaises(ValueError):
                pipeline.stage(root, state, "literature", [], timeout=1, runner=lambda *a: 1)
            self.assertEqual(json.loads((root / "pipeline.json").read_text())["stages"]["literature"]["state"], "failed")
            def interrupt(*args): raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt): pipeline.stage(root, state, "reader", [], timeout=1, runner=interrupt)
            self.assertEqual(json.loads((root / "pipeline.json").read_text())["stages"]["reader"]["state"], "running")

    def test_single_entry_commands_order_and_no_planner_for_saved_plan(self):
        with TemporaryDirectory() as directory:
            root, state, args, _ = self.setup_files(directory)
            calls = []
            def runner(cmd, log, timeout): calls.append(cmd); return 0
            fake_record = {"acceptance": {"singleFreshEndToEndRun": False}}
            with patch.object(pipeline, "prepare_reader_input"), \
                 patch.object(pipeline, "read_json", wraps=pipeline.read_json) as read, \
                 patch.object(pipeline, "assemble", return_value=fake_record), \
                 patch.object(pipeline, "render", return_value="Offline substituted report"):
                (Path(directory) / "proof.json").write_text("[]")
                args.pdf_proof = Path(directory) / "proof.json"
                result = pipeline.execute_pipeline(root, state, args, runner=runner)
            self.assertEqual([Path(cmd[3]).name for cmd in calls], ["run_aip_literature.py", "run_aip_reader.py"])
            self.assertNotIn("planner", state["stages"])
            self.assertTrue(result["pipeline"]["singleEntryCompleted"])
            self.assertFalse(result["pipeline"]["platformDiscoveryUsed"])
            self.assertFalse(result["acceptance"]["singleFreshEndToEndRun"])

    def test_failed_search_stops_pdf_and_reader(self):
        with TemporaryDirectory() as directory:
            root, state, args, _ = self.setup_files(directory)
            calls = []
            def runner(*cmd): calls.append(cmd); return 1
            with self.assertRaises(ValueError): pipeline.execute_pipeline(root, state, args, runner=runner)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("reader", state["stages"])

    def test_missing_or_wrong_pdf_stops_before_reader_and_retains_search(self):
        with TemporaryDirectory() as directory:
            root, state, args, _ = self.setup_files(directory)
            with patch.object(pipeline, "prepare_reader_input", side_effect=ValueError("bad proof")):
                with self.assertRaises(ValueError): pipeline.execute_pipeline(root, state, args, runner=lambda *a: 0)
            self.assertEqual(state["stages"]["literature"]["state"], "completed")
            self.assertNotIn("reader", state["stages"])

    def test_resume_completed_search_only_runs_reader(self):
        with TemporaryDirectory() as directory:
            root, state, args, _ = self.setup_files(directory)
            state["stages"]["literature"] = {"state": "completed"}
            args.pdf_proof = Path(directory) / "proof.json"; args.pdf_proof.write_text("[]")
            calls = []
            with patch.object(pipeline, "prepare_reader_input"), patch.object(pipeline, "assemble", return_value={}), \
                 patch.object(pipeline, "render", return_value="Offline"):
                pipeline.execute_pipeline(root, state, args, runner=lambda cmd, *a: calls.append(cmd) or 0)
            self.assertEqual(len(calls), 1)
            self.assertIn("run_aip_reader.py", str(calls[0]))

    def test_plan_tampering_blocks_before_commands(self):
        with TemporaryDirectory() as directory:
            root, state, args, _ = self.setup_files(directory)
            state["planSha256"] = "a" * 64
            with self.assertRaises(ValueError):
                pipeline.execute_pipeline(root, state, args, runner=lambda *a: self.fail("must not execute"))

    def test_lock_prevents_concurrent_resume_and_cleans_owned_lock(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with pipeline.checkpoint_lock(root):
                with self.assertRaises(FileExistsError):
                    with pipeline.checkpoint_lock(root): self.fail("concurrent run")
            self.assertFalse((root / "pipeline.lock").exists())

    def test_default_no_execute_calls_nothing(self):
        with patch.object(pipeline, "execute_pipeline", side_effect=AssertionError("paid")), \
             patch.object(pipeline.subprocess, "Popen", side_effect=AssertionError("process")):
            self.assertEqual(pipeline.main(["--query", "anything"]), 0)
        with patch.object(aip_pdf_worker, "resolve_source", side_effect=AssertionError("PDF network")):
            self.assertEqual(aip_pdf_worker.main(["--source", ".", "--output", "no-write.json",
                                                  "--cache", ".", "--search-id", "S1", "--reader-id", "S2"]), 0)

    def test_fresh_query_planner_called_once_and_plan_saved_before_search_failure(self):
        with TemporaryDirectory() as directory:
            root, state, args, plan = self.setup_files(directory)
            (root / "plan.json").unlink(); state["query"] = "new question"
            calls = []
            async def planner(query, **kwargs): calls.append(query); return plan, {"requestCount": 1}
            with self.assertRaises(ValueError):
                pipeline.execute_pipeline(root, state, args, planner=planner, runner=lambda *a: 1)
            self.assertEqual(calls, ["new question"])
            self.assertTrue((root / "plan.json").is_file())
            self.assertEqual(state["stages"]["planner"]["state"], "completed")

    def test_failed_planner_never_automatically_replayed(self):
        with TemporaryDirectory() as directory:
            root, state, args, _ = self.setup_files(directory)
            (root / "plan.json").unlink(); state["stages"]["planner"] = {"state": "running"}
            with self.assertRaises(ValueError): pipeline.execute_pipeline(root, state, args)

    def test_complete_pipeline_uses_real_binding_and_assembly_with_synthetic_checkpoints(self):
        with TemporaryDirectory() as directory:
            root, state, args, _ = self.setup_files(directory)
            source, reader = Path(directory) / "source", Path(directory) / "reader"
            cache = root / "outputs/pdf-cache"; cache.mkdir(parents=True)
            for pdf in (Path(directory) / "cache").glob("*.pdf"):
                shutil.copyfile(pdf, cache / pdf.name)
            args.pdf_proof = Path(directory) / "proof.json"
            calls = []
            def runner(cmd, *unused):
                calls.append(Path(cmd[3]).name)
                target = Path(cmd[cmd.index("--record-dir") + 1])
                shutil.copytree(source if "run_aip_literature.py" in calls[-1] else reader, target)
                if calls[-1] == "run_aip_reader.py":
                    prepared = pipeline.prepare_reader_input(root / "literature", root / "pdf_resolution.json", cache_dir=cache)
                    (target / "reader_input.json").write_text(json.dumps(prepared))
                    journal = json.loads((target / "reader_journal.json").read_text())
                    journal["pdf"] = prepared["pdf"]
                    (target / "reader_journal.json").write_text(json.dumps(journal))
                return 0
            with patch.object(pipeline, "ROOT", root):
                result = pipeline.execute_pipeline(root, state, args, runner=runner)
                result_again = pipeline.execute_pipeline(root, state, args, runner=runner)
            self.assertEqual(calls, ["run_aip_literature.py", "run_aip_reader.py"])
            self.assertEqual(result["executionRecords"][1]["subtaskId"], "S2")
            self.assertTrue(result_again["pipeline"]["singleEntryCompleted"])
            # Records are synthetic substitutes in this TEST, not real scientific evidence.


class PdfWorkerTests(unittest.TestCase):
    def test_known_sources_preserved_when_existing_pdf_needs_independent_validation(self):
        with TemporaryDirectory() as directory:
            source, _, _ = assembly_fixture.fixture.ReaderInputTests().make_files(directory)
            url = "https://arxiv.org/pdf/1711.02132"
            for name in ("outcome", "backend_outcome"):
                path = source / (name + ".json"); data = json.loads(path.read_text())
                result = data["execution"]["result"] if name == "outcome" else data["result"]
                result["items"][0]["data"].update(pdfUrl=url, sourceUrls=["https://arxiv.org/abs/1711.02132"])
                path.write_text(json.dumps(data))
            observed = []
            class Resolver:
                def __init__(self, cache): pass
                async def resolve(self, task, result):
                    observed.append((task, result))
                    return task, [{"state": "verified"}]
            with patch.object(aip_pdf_worker, "ArxivPdfResolver", Resolver):
                asyncio.run(aip_pdf_worker.resolve_source(source, Path(directory) / "out.json", Path(directory), "S1", "S2"))
            self.assertIsNone(observed[0][0]["input"]["papers"][0]["pdfUrl"])
            self.assertIsNone(observed[0][1]["items"][0]["data"]["pdfUrl"])
            self.assertEqual(observed[0][1]["items"][0]["data"]["sourceUrls"], ["https://arxiv.org/abs/1711.02132"])

    def test_unavailable_pdf_stops_without_reader_call(self):
        with TemporaryDirectory() as directory:
            source, _, _ = assembly_fixture.fixture.ReaderInputTests().make_files(directory)
            class Resolver:
                def __init__(self, cache): pass
                async def resolve(self, task, result): return task, [{"state": "unavailable"}]
            with patch.object(aip_pdf_worker, "ArxivPdfResolver", Resolver), self.assertRaises(ValueError):
                asyncio.run(aip_pdf_worker.resolve_source(source, Path(directory) / "out.json", Path(directory), "S1", "S2"))


class EvidenceExportTests(unittest.TestCase):
    def test_pipeline_report_distinguishes_saved_plan_from_historical_assembly(self):
        from assemble_aip_evidence import assemble, render
        with TemporaryDirectory() as directory:
            source, reader, cache = assembly_fixture.AssemblyTests().files(directory)
            record = assemble(source, reader, cache_dir=cache)
            self.assertIn("没有重新执行科研任务", render(record))
            record["pipeline"] = {"scope": "LOCAL_AIP_SINGLE_ENTRY_SEARCH_READER",
                                  "singleEntryCompleted": True, "plannerExecutedByPipeline": False}
            report = render(record)
            self.assertIn("复用已保存的计划", report)
            self.assertNotIn("没有重新执行科研任务", report)
            self.assertIn("不是正式平台端到端验收", report)
            record["pipeline"]["plannerExecutedByPipeline"] = True
            self.assertIn("包含真实模型规划", render(record))

    def test_allowlist_excludes_credentials_paths_raw_text_and_identities(self):
        with TemporaryDirectory() as directory:
            source, reader, cache = assembly_fixture.AssemblyTests().files(directory)
            from assemble_aip_evidence import assemble
            record = assemble(source, reader, cache_dir=cache)
            record.update(apiKey="SECRET_SENTINEL", extra="PRIVATE_SENTINEL")
            record["executionRecords"][0]["result"]["items"][0]["data"]["private"] = "SECRET_SENTINEL"
            regression = {"testsPassed": 404, "testsSkipped": 0, "httpChecksPassed": True,
                          "scenarios": [{"passed": True}], "env": "SECRET_SENTINEL"}
            value = sanitized_evidence(record, regression)
            text = json.dumps(value)
            self.assertNotIn("SECRET_SENTINEL", text); self.assertNotIn("PRIVATE_SENTINEL", text)
            self.assertNotIn(directory, text); self.assertNotIn("aip-reader-task", text)
            self.assertFalse(value["platformDiscoveryVerified"])
            self.assertEqual(value["tasks"][1]["evidenceCount"], 1)

    def test_unverified_regression_rejected(self):
        with self.assertRaises(ValueError): sanitized_evidence({"acceptance": {"passed": True}}, {"testsPassed": False})

    def test_export_does_not_overwrite(self):
        import export_team_evidence as export
        with TemporaryDirectory() as directory:
            path = Path(directory) / "exists.json"; path.write_text("original")
            with patch.object(export, "assemble", return_value={}), patch.object(export, "read_json", return_value={}), \
                 patch.object(export, "sanitized_evidence", return_value={}):
                with self.assertRaises(FileExistsError):
                    export.main(["--search", ".", "--reader", ".", "--regression", "dummy", "--output", str(path)])
            self.assertEqual(path.read_text(), "original")


class SyncAuditTests(unittest.TestCase):
    def test_secret_is_flagged_without_returning_its_value(self):
        from scripts.prepare_team_sync import classify
        self.assertEqual(classify("module.py", "sensitive-placeholder", ["sensitive-placeholder"]), "POSSIBLE_SECRET_DO_NOT_UPLOAD")
        self.assertEqual(classify("source.py", "sk-" + "a" * 25, []), "POSSIBLE_SECRET_DO_NOT_UPLOAD")
        self.assertEqual(classify("config.py", "-----BEGIN " + "PRIVATE KEY-----", []), "POSSIBLE_SECRET_DO_NOT_UPLOAD")

    def test_env_and_certificates_forbidden_example_allowed(self):
        from scripts.prepare_team_sync import classify
        for path in (".env", "config/.env.local", "config/client.pem", "outputs/result.json", ".venv/lib/site.py"):
            self.assertEqual(classify(path, None, []), "DO_NOT_UPLOAD")
        self.assertEqual(classify(".env.example", "API_KEY=", []), "CANDIDATE_REQUIRES_DIFF_REVIEW")

    def test_paper_pdf_requires_license_check(self):
        from scripts.prepare_team_sync import classify
        self.assertEqual(classify("examples/reader_test.pdf", None, []), "REVIEW_PDF_LICENSE_AND_PROVENANCE")
